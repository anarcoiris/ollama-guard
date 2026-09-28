"""Multi-port ASGI launcher for agent API and lane mirrors."""

from __future__ import annotations

import asyncio
import os
import sys

import uvicorn

from guard.config import load_config
from guard.main import app, listen_port_var


class PortAwareMiddleware:
    """Inject listen port into context for lane mirror routing."""

    def __init__(self, app, port: int):
        self.app = app
        self.port = port

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            token = listen_port_var.set(self.port)
            try:
                await self.app(scope, receive, send)
            finally:
                listen_port_var.reset(token)
        else:
            await self.app(scope, receive, send)


async def _serve_port(port: int) -> None:
    wrapped = PortAwareMiddleware(app, port)
    config = uvicorn.Config(wrapped, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


async def main() -> None:
    cfg = load_config()
    ports = [cfg.agent_port]
    if cfg.lane_mirror_ports:
        for lane in cfg.lanes.values():
            if lane.port not in ports:
                ports.append(lane.port)

    override = os.getenv("GUARD_PORTS")
    if override:
        ports = [int(p.strip()) for p in override.split(",")]

    print(f"Ollama Guard listening on {ports}", flush=True)
    await asyncio.gather(*[_serve_port(p) for p in ports])


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
