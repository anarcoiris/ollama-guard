"""FastAPI application - agent port + lane mirror routing with dynamic multi-GPU pool dispatch."""

from __future__ import annotations

import os
from contextvars import ContextVar
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from guard.admin import init_admin, router as admin_router
from guard.config import GuardConfig, load_config
from guard.proxy import proxy_request
from guard.queue import AdmissionController, AdmissionDenied
from guard.router import resolve_request_lane

listen_port_var: ContextVar[int | None] = ContextVar("listen_port", default=None)

app = FastAPI(title="Ollama Guard", version="0.1.0")
app.include_router(admin_router)

_config: GuardConfig | None = None
_controller: AdmissionController | None = None

PROXY_PATHS = (
    "/api/chat",
    "/api/generate",
    "/api/embeddings",
    "/v1/chat/completions",
    "/v1/completions",
    "/v1/embeddings",
)


@app.on_event("startup")
async def startup() -> None:
    global _config, _controller
    schema = os.getenv("GUARD_SCHEMA")
    defaults = os.getenv("GUARD_DEFAULTS")
    _config = load_config(
        schema_path=__import__("pathlib").Path(schema) if schema else None,
        defaults_path=__import__("pathlib").Path(defaults) if defaults else None,
    )
    _controller = AdmissionController(_config)
    init_admin(_config, _controller)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/status")
async def public_status() -> dict[str, Any]:
    if _controller is None or _config is None:
        return {"status": "initializing"}
    snap = _controller.status_snapshot()
    snap["agent_port"] = _config.agent_port
    return snap


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def catch_all(request: Request, path: str) -> Response:
    if _config is None or _controller is None:
        return JSONResponse({"error": "not ready"}, status_code=503)

    full_path = f"/{path}"
    if full_path.startswith("/admin"):
        return JSONResponse({"error": "use admin router"}, status_code=404)

    if request.method == "GET" and full_path in ("/", "/api/tags", "/api/version"):
        return await _passthrough_or_tags(request, full_path)

    if not any(full_path.startswith(p) for p in PROXY_PATHS):
        if request.method == "GET":
            return await _passthrough_or_tags(request, full_path)
        return JSONResponse({"error": f"unsupported path {full_path}"}, status_code=404)

    body = await request.body()
    listen_port = listen_port_var.get()
    lane, model, mismatch = resolve_request_lane(_config, request, body, listen_port)

    if lane is None:
        return JSONResponse({"error": mismatch or "lane not resolved"}, status_code=400)
    if mismatch:
        return JSONResponse(
            {"error": mismatch},
            status_code=409,
            headers={"Retry-After": "30"},
        )

    client_name = request.headers.get("x-guard-client", "unknown")
    model_name = model or lane.tag

    if _controller.draining:
        return JSONResponse(
            {"error": "Guard is draining"},
            status_code=503,
            headers={"Retry-After": "60"},
        )

    try:
        ticket = await _controller.acquire(
            lane,
            model=model_name,
            client=client_name,
            timeout=float(os.getenv("GUARD_QUEUE_TIMEOUT", "600")),
        )
    except AdmissionDenied as exc:
        return JSONResponse(
            {"error": str(exc)},
            status_code=503,
            headers={"Retry-After": str(exc.retry_after)},
        )

    actual_lane = _config.lanes.get(ticket.lane_name, lane)
    released = False

    async def release_ticket() -> None:
        nonlocal released
        if not released:
            released = True
            await _controller.release(ticket)

    try:
        return await proxy_request(
            request,
            actual_lane,
            body,
            cch_normalize=_config.cch_normalize,
            client_name=client_name,
            ticket_id=ticket.ticket_id,
            on_release=release_ticket,
        )
    except Exception:
        await release_ticket()
        raise


async def _passthrough_or_tags(request: Request, path: str) -> Response:
    assert _config is not None
    listen_port = listen_port_var.get()
    lane, _, _ = resolve_request_lane(_config, request, b"", listen_port)
    if lane is None:
        lane = next(iter(_config.lanes.values()), None)
    if lane is None:
        return JSONResponse({"error": "no lanes configured"}, status_code=503)
    return await proxy_request(
        request,
        lane,
        await request.body(),
        cch_normalize=False,
        client_name="passthrough",
        ticket_id="passthrough",
    )


def create_app() -> FastAPI:
    return app
