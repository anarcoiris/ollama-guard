"""Per-lane FIFO queue, semaphores, and GPU group locks with dynamic pool fallback."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from guard.config import GuardConfig, LaneConfig


@dataclass
class QueueTicket:
    ticket_id: str
    lane_name: str
    model: str
    client: str
    enqueued_at: float
    event: asyncio.Event = field(default_factory=asyncio.Event)
    admitted: bool = False
    error: str | None = None


@dataclass
class LaneState:
    lane: LaneConfig
    in_flight: int = 0
    queue: asyncio.Queue[QueueTicket] = field(default_factory=asyncio.Queue)
    waiting: list[QueueTicket] = field(default_factory=list)


class AdmissionController:
    """Coordinates lane semaphores, shared GPU group tokens, and multi-GPU pool balancing."""

    def __init__(self, config: GuardConfig) -> None:
        self.config = config
        self.draining = False
        self._lock = asyncio.Lock()
        self._lanes: dict[str, LaneState] = {
            name: LaneState(lane=lane) for name, lane in config.lanes.items()
        }
        self._gpu_in_use: dict[str, set[str]] = {
            g: set() for g in {l.gpu_group for l in config.lanes.values()}
        }
        self._active: dict[str, QueueTicket] = {}

    def lane_state(self, name: str) -> LaneState | None:
        return self._lanes.get(name)

    def all_states(self) -> dict[str, LaneState]:
        return self._lanes

    def set_draining(self, value: bool) -> None:
        self.draining = value

    def total_in_flight(self) -> int:
        return sum(s.in_flight for s in self._lanes.values())

    def status_snapshot(self) -> dict[str, Any]:
        lanes = {}
        for name, state in self._lanes.items():
            lanes[name] = {
                "endpoint_key": state.lane.endpoint_key,
                "port": state.lane.port,
                "pinned_model": state.lane.tag,
                "max_parallel": state.lane.max_parallel,
                "in_flight": state.in_flight,
                "queue_depth": len(state.waiting),
                "gpu_group": state.lane.gpu_group,
            }
        return {
            "draining": self.draining,
            "total_in_flight": self.total_in_flight(),
            "lanes": lanes,
            "gpu_groups": {g: list(lanes_using) for g, lanes_using in self._gpu_in_use.items()},
        }

    def _gpu_blocked(self, lane: LaneConfig) -> bool:
        if not self.config.gpu_group_exclusive:
            return False
        group = lane.gpu_group
        others = self._gpu_in_use.get(group, set())
        if not others:
            return False
        if lane.name in others and len(others) == 1:
            return False
        if len(others) >= 1 and lane.name not in others:
            return True
        return False

    def _can_admit(self, lane: LaneConfig) -> bool:
        state = self._lanes[lane.name]
        if state.in_flight >= lane.max_parallel:
            return False
        if self._gpu_blocked(lane):
            return False
        return True

    def _find_free_compatible_lane(self, lane: LaneConfig, model: str) -> LaneConfig | None:
        """Return preferred lane if free, or a free sibling lane hosting the exact same model."""
        if self._can_admit(lane):
            return lane
        for candidate in self.config.lanes.values():
            if candidate.name != lane.name and validate_model_for_lane(candidate, model, self.config.legacy_tags):
                if self._can_admit(candidate):
                    return candidate
        return None

    async def acquire(
        self,
        lane: LaneConfig,
        *,
        model: str,
        client: str = "unknown",
        timeout: float | None = 300.0,
    ) -> QueueTicket:
        if self.draining:
            raise AdmissionDenied("Guard is draining; not accepting new work", retry_after=30)

        ticket = QueueTicket(
            ticket_id=str(uuid.uuid4())[:8],
            lane_name=lane.name,
            model=model,
            client=client,
            enqueued_at=time.time(),
        )

        async with self._lock:
            admissible_lane = self._find_free_compatible_lane(lane, model)
            if admissible_lane:
                self._admit_locked(ticket, admissible_lane)
                return ticket
            if len(self._lanes[lane.name].waiting) >= self.config.max_queue_size:
                raise AdmissionDenied("Queue full", retry_after=60)

        state = self._lanes[lane.name]
        state.waiting.append(ticket)
        try:
            if timeout is not None:
                await asyncio.wait_for(self._wait_for_admission(ticket, lane), timeout=timeout)
            else:
                await self._wait_for_admission(ticket, lane)
        except asyncio.TimeoutError as exc:
            if ticket in state.waiting:
                state.waiting.remove(ticket)
            raise AdmissionDenied("Queue wait timeout", retry_after=30) from exc

        if ticket.error:
            raise AdmissionDenied(ticket.error, retry_after=30)
        return ticket

    async def _wait_for_admission(self, ticket: QueueTicket, lane: LaneConfig) -> None:
        while not ticket.admitted:
            await asyncio.sleep(0.05)
            async with self._lock:
                if ticket.admitted:
                    return
                if ticket not in self._lanes[lane.name].waiting:
                    return
                if self.draining:
                    ticket.error = "Drain cancelled queued work"
                    self._lanes[lane.name].waiting.remove(ticket)
                    return
                # Check preferred lane first
                if self._can_admit(lane) and self._lanes[lane.name].waiting[0] is ticket:
                    self._lanes[lane.name].waiting.pop(0)
                    self._admit_locked(ticket, lane)
                    return
                # Check compatible sibling lanes
                for candidate in self.config.lanes.values():
                    if candidate.name != lane.name and validate_model_for_lane(candidate, ticket.model, self.config.legacy_tags):
                        if self._can_admit(candidate) and self._lanes[lane.name].waiting[0] is ticket:
                            self._lanes[lane.name].waiting.pop(0)
                            self._admit_locked(ticket, candidate)
                            return

    def _admit_locked(self, ticket: QueueTicket, lane: LaneConfig) -> None:
        ticket.lane_name = lane.name
        state = self._lanes[lane.name]
        state.in_flight += 1
        ticket.admitted = True
        self._active[ticket.ticket_id] = ticket
        self._gpu_in_use.setdefault(lane.gpu_group, set()).add(lane.name)

    async def release(self, ticket: QueueTicket) -> None:
        async with self._lock:
            state = self._lanes.get(ticket.lane_name)
            if not state:
                return
            if state.in_flight > 0:
                state.in_flight -= 1
            self._active.pop(ticket.ticket_id, None)
            lane = state.lane
            group = self._gpu_in_use.get(lane.gpu_group, set())
            if state.in_flight == 0 and lane.name in group:
                group.discard(lane.name)
            if not group:
                self._gpu_in_use[lane.gpu_group] = set()

    async def wait_drained(self, timeout: float = 1800.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.total_in_flight() == 0 and not any(s.waiting for s in self._lanes.values()):
                return True
            await asyncio.sleep(0.5)
        return self.total_in_flight() == 0


class AdmissionDenied(Exception):
    def __init__(self, message: str, *, retry_after: int = 30) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def validate_model_for_lane(lane: LaneConfig, model: str, legacy_tags: dict[str, str]) -> bool:
    """Return True if model matches lane pin (allows legacy aliases)."""
    normalized = model.lower().split(":")[0]
    pinned = lane.tag.lower()
    if normalized == pinned or model.lower() == pinned:
        return True
    if model.lower() in {k.lower() for k in legacy_tags}:
        canonical = legacy_tags.get(model, legacy_tags.get(model.lower(), ""))
        if canonical.lower() == lane.tag.lower():
            return True
    if pinned in model.lower() or model.lower() in pinned:
        return True
    if "qwen3.8" in pinned and "qwen3.8" in model.lower():
        return lane.endpoint_key in ("planner", "atomic")
    if "coder" in pinned and ("coder" in model.lower() or "qwen2.5-coder" in model.lower()):
        return lane.endpoint_key in ("coder", "paracoder")
    if "planner" in pinned or "vibethinker" in model.lower():
        return lane.endpoint_key == "planner"
    if "chat-analyzer" in pinned and "chat-analyzer" in model.lower():
        return lane.endpoint_key == "chat"
    return False
