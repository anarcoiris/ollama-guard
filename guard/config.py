"""Load lane schema from r1c-v3.schema.yaml, registry.yaml, or guard.defaults.yaml."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PORT_TO_ENDPOINT_KEY = {
    11434: "planner",
    11435: "chat",
    11436: "coder",
    11437: "paracoder",
    11438: "nomic",
    11439: "atomic",
    11800: "agent",
}

LANE_TO_ENDPOINT_KEY = {
    "integrator": "chat",
    "serializer": "planner",
    "executor": "coder",
    "executor_parallel": "paracoder",
    "atomic": "atomic",
    "nomic": "nomic",
}

ENDPOINT_KEY_ALIASES = {
    "intake": "chat",
    "integrator": "chat",
    "serializer": "planner",
    "executor": "coder",
    "executor_parallel": "paracoder",
    "planner": "planner",
    "orch": "planner",
    "orchestrator": "planner",
    "atomic": "atomic",
    "coder": "coder",
    "chat": "chat",
    "paracoder": "paracoder",
    "nomic": "nomic",
    "embed": "nomic",
    "embeddings": "nomic",
}


@dataclass
class LaneConfig:
    name: str
    endpoint_key: str
    tag: str
    port: int
    container: str
    max_parallel: int
    gpu_group: str
    backend_url: str


@dataclass
class GuardConfig:
    agent_port: int = 11800
    lane_mirror_ports: bool = True
    max_queue_size: int = 64
    cch_normalize: bool = True
    gpu_group_exclusive: bool = True
    backends_mode: str = "docker"  # docker | host
    lanes: dict[str, LaneConfig] = field(default_factory=dict)
    legacy_tags: dict[str, str] = field(default_factory=dict)
    port_to_lane: dict[int, str] = field(default_factory=dict)
    model_to_lane: dict[str, str] = field(default_factory=dict)

    def lane_for_port(self, port: int) -> str | None:
        return self.port_to_lane.get(port)

    def lane_for_model(self, model: str) -> str | None:
        normalized = _normalize_model(model)
        if normalized in self.model_to_lane:
            return self.model_to_lane[normalized]
        lower = normalized.lower()
        for tag, lane_name in self.model_to_lane.items():
            if tag.lower() == lower:
                return lane_name
        for fragment, lane_name in self.model_to_lane.items():
            if fragment.lower() in lower or lower in fragment.lower():
                return lane_name
        return None

    def resolve_lane(
        self,
        *,
        port: int | None = None,
        lane_hint: str | None = None,
        model: str | None = None,
    ) -> LaneConfig | None:
        lane_name: str | None = None
        if port and port in self.port_to_lane and port != self.agent_port:
            lane_name = self.port_to_lane[port]
        elif lane_hint:
            key = ENDPOINT_KEY_ALIASES.get(lane_hint.lower(), lane_hint.lower())
            for name, lane in self.lanes.items():
                if lane.endpoint_key == key or name == lane_hint.lower():
                    lane_name = name
                    break
        elif model:
            resolved = self.lane_for_model(model)
            if resolved:
                lane_name = resolved
        return self.lanes.get(lane_name) if lane_name else None


def _normalize_model(model: str) -> str:
    return model.split(":")[0] if ":" in model and model.count(":") == 1 else model


def _gpu_group_for_lane(name: str, lane_data: dict[str, Any]) -> str:
    gpu = str(lane_data.get("gpu", ""))
    if "1070 #1" in gpu or "1070 #1" in gpu.replace("GTX ", "") or "gpu_1070_1" in gpu:
        return "gpu_1070_1"
    if "1070 #2" in gpu or "gpu_1070_2" in gpu:
        return "gpu_1070_2"
    if "1080" in gpu or "gpu_1080" in gpu:
        return "gpu_1080"
    return f"gpu_{name}"


def _backend_url(container: str, port: int, mode: str) -> str:
    if mode == "docker":
        return f"http://{container}:11434"
    return f"http://127.0.0.1:{port}"


def load_config(
    schema_path: Path | None = None,
    registry_path: Path | None = None,
    defaults_path: Path | None = None,
) -> GuardConfig:
    schema_path = schema_path or Path(os.getenv("GUARD_SCHEMA", "/config/schema.yaml"))
    registry_path = registry_path or Path(os.getenv("GUARD_REGISTRY", "/config/registry.yaml"))
    defaults_path = defaults_path or Path(
        os.getenv("GUARD_DEFAULTS", str(Path(__file__).resolve().parent.parent / "config" / "guard.defaults.yaml"))
    )

    raw: dict[str, Any] = {}
    if schema_path.is_file():
        raw = yaml.safe_load(schema_path.read_text(encoding="utf-8")) or {}
    elif defaults_path.is_file():
        raw = yaml.safe_load(defaults_path.read_text(encoding="utf-8")) or {}
    else:
        raise FileNotFoundError(f"No schema at {schema_path} or defaults at {defaults_path}")

    mode = os.getenv("GUARD_BACKENDS_MODE", "host" if os.name == "nt" else "docker")
    cfg = GuardConfig(
        agent_port=int(os.getenv("GUARD_AGENT_PORT", raw.get("agent_port", 11800))),
        lane_mirror_ports=os.getenv("GUARD_LANE_MIRRORS", "1") == "1",
        max_queue_size=int(os.getenv("GUARD_MAX_QUEUE", raw.get("max_queue_size", 64))),
        cch_normalize=os.getenv("GUARD_CCH_NORMALIZE", "1" if raw.get("cch_normalize", True) else "0") == "1",
        gpu_group_exclusive=os.getenv("GUARD_GPU_GROUP_EXCLUSIVE", "1") == "1",
        backends_mode=mode,
    )

    lanes_raw = raw.get("lanes", {})
    if not lanes_raw and raw.get("single"):
        lanes_raw = raw["single"].get("lanes", {})

    for name, lane_data in lanes_raw.items():
        port = int(lane_data["port"])
        container = str(lane_data.get("container", f"ollama-{name}"))
        endpoint_key = str(
            lane_data.get("endpoint_key")
            or LANE_TO_ENDPOINT_KEY.get(name)
            or PORT_TO_ENDPOINT_KEY.get(port, name)
        )
        max_parallel = int(lane_data.get("max_parallel", lane_data.get("ollama_num_parallel", 1)))
        if max_parallel == 0:
            max_parallel = 1
        gpu_group = str(lane_data.get("gpu_group", _gpu_group_for_lane(name, lane_data)))
        backend = _backend_url(container, port, cfg.backends_mode)
        lane = LaneConfig(
            name=name,
            endpoint_key=endpoint_key,
            tag=str(lane_data["tag"]),
            port=port,
            container=container,
            max_parallel=max_parallel,
            gpu_group=gpu_group,
            backend_url=backend,
        )
        cfg.lanes[name] = lane
        cfg.port_to_lane[port] = name
        # Prefer primary executor over executor_parallel for shared tags
        existing = cfg.model_to_lane.get(lane.tag)
        if existing is None or name in ("planner", "serializer", "atomic"):
            cfg.model_to_lane[lane.tag] = name

    legacy = raw.get("legacy_tags", {})
    cfg.legacy_tags = {str(k): str(v) for k, v in legacy.items()}
    for alias, canonical in cfg.legacy_tags.items():
        for lane in cfg.lanes.values():
            if lane.tag == canonical:
                cfg.model_to_lane[alias] = lane.name
                break

    # Model fragments for fuzzy routing
    for lane in cfg.lanes.values():
        if "coder" in lane.tag.lower() or "qwen2.5-coder" in lane.tag.lower():
            cfg.model_to_lane.setdefault("qwen2.5-coder", lane.name)
        if "planner" in lane.tag.lower() or "vibethinker" in lane.tag.lower() or "qwen3" in lane.tag.lower():
            cfg.model_to_lane.setdefault("vibethinker", lane.name)
            cfg.model_to_lane.setdefault("planner_explorer", lane.name)
            cfg.model_to_lane.setdefault("qwen3.8", lane.name)
            cfg.model_to_lane.setdefault("qwen3.8-128k", lane.name)
            cfg.model_to_lane.setdefault("qwen3.8-64k", lane.name)
        if "chat-analyzer" in lane.tag.lower():
            cfg.model_to_lane.setdefault("chat-analyzer", lane.name)

    return cfg
