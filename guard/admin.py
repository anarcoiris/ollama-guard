"""Admin endpoints: drain, status, backend health."""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from guard.config import GuardConfig
from guard.queue import AdmissionController

router = APIRouter(prefix="/admin", tags=["admin"])

_state: AdmissionController | None = None
_config: GuardConfig | None = None


def init_admin(config: GuardConfig, controller: AdmissionController) -> None:
    global _state, _config
    _config = config
    _state = controller


class DrainRequest(BaseModel):
    timeout: float = Field(default=1800.0, description="Seconds to wait for in-flight work")


@router.get("/status")
async def status() -> dict[str, Any]:
    if _state is None or _config is None:
        raise HTTPException(503, "Guard not initialized")
    snap = _state.status_snapshot()
    snap["agent_port"] = _config.agent_port
    snap["backends"] = await _backend_health(_config)
    return snap


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/drain")
async def drain(body: DrainRequest | None = None) -> dict[str, Any]:
    if _state is None:
        raise HTTPException(503, "Guard not initialized")
    _state.set_draining(True)
    timeout = body.timeout if body else 1800.0
    drained = await _state.wait_drained(timeout=timeout)
    return {
        "draining": True,
        "drained": drained,
        "in_flight": _state.total_in_flight(),
    }


@router.post("/undrain")
async def undrain() -> dict[str, Any]:
    if _state is None:
        raise HTTPException(503, "Guard not initialized")
    _state.set_draining(False)
    return {"draining": False}


@router.post("/recycle")
async def recycle(body: DrainRequest | None = None) -> dict[str, Any]:
    """Drain queue; host script performs docker recreate then undrain."""
    if _state is None:
        raise HTTPException(503, "Guard not initialized")
    _state.set_draining(True)
    timeout = body.timeout if body else 1800.0
    drained = await _state.wait_drained(timeout=timeout)
    script = os.getenv("GUARD_RECYCLE_SCRIPT", "")
    ran = False
    error: str | None = None
    if script and os.path.isfile(script):
        proc = await asyncio.create_subprocess_shell(
            script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        ran = proc.returncode == 0
        if not ran:
            error = stderr.decode() or stdout.decode()
    _state.set_draining(False)
    return {
        "drained": drained,
        "recycle_script_ran": ran,
        "recycle_error": error,
        "draining": False,
    }


async def _backend_health(config: GuardConfig) -> dict[str, Any]:
    results: dict[str, Any] = {}
    async with httpx.AsyncClient(timeout=5.0) as client:
        for name, lane in config.lanes.items():
            url = f"{lane.backend_url.rstrip('/')}/api/tags"
            try:
                resp = await client.get(url)
                results[name] = {"ok": resp.status_code == 200, "url": lane.backend_url}
            except httpx.HTTPError as exc:
                results[name] = {"ok": False, "url": lane.backend_url, "error": str(exc)}
    return results
