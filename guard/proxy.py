"""Streaming and non-streaming proxy to Ollama backends with full stream lifecycle ticket locking."""

from __future__ import annotations

import json
import re
import time
from typing import Any, AsyncIterator, Callable, Coroutine

import httpx
from fastapi import Request, Response
from fastapi.responses import StreamingResponse

from guard.config import LaneConfig
from guard.metrics import log_metric

CCH_PATTERN = re.compile(r"cch=[0-9a-f]+;", re.IGNORECASE)


def normalize_cch(body: bytes) -> bytes:
    text = body.decode("utf-8", errors="replace")
    return CCH_PATTERN.sub("cch=00000;", text).encode("utf-8")


async def proxy_request(
    request: Request,
    lane: LaneConfig,
    body: bytes,
    *,
    cch_normalize: bool,
    client_name: str,
    ticket_id: str,
    metrics_path: str | None = None,
    on_release: Callable[[], Coroutine[Any, Any, None]] | None = None,
) -> Response:
    if cch_normalize and request.method == "POST" and body:
        body = normalize_cch(body)

    url = f"{lane.backend_url.rstrip('/')}{request.url.path}"
    if request.url.query:
        url = f"{url}?{request.url.query}"

    headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in ("host", "content-length", "connection")
    }
    headers["host"] = lane.backend_url.replace("http://", "").replace("https://", "")

    stream = _wants_stream(body, request)
    start = time.time()

    if stream:
        return await _stream_proxy(
            request.method,
            url,
            headers,
            body,
            lane=lane,
            client_name=client_name,
            ticket_id=ticket_id,
            start=start,
            metrics_path=metrics_path,
            on_release=on_release,
        )
    return await _buffered_proxy(
        request.method,
        url,
        headers,
        body,
        lane=lane,
        client_name=client_name,
        ticket_id=ticket_id,
        start=start,
        metrics_path=metrics_path,
        on_release=on_release,
    )


def _wants_stream(body: bytes, request: Request) -> bool:
    if request.headers.get("accept") == "text/event-stream":
        return True
    if not body:
        return False
    try:
        data = json.loads(body)
        if isinstance(data, dict) and data.get("stream") is True:
            return True
    except json.JSONDecodeError:
        pass
    return False


async def _buffered_proxy(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes,
    *,
    lane: LaneConfig,
    client_name: str,
    ticket_id: str,
    start: float,
    metrics_path: str | None,
    on_release: Callable[[], Coroutine[Any, Any, None]] | None = None,
) -> Response:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(3600.0, connect=30.0)) as client:
            resp = await client.request(method, url, headers=headers, content=body)
    finally:
        if on_release:
            try:
                await on_release()
            except Exception:
                pass

    elapsed_ms = int((time.time() - start) * 1000)
    log_metric(
        {
            "event": "proxy_complete",
            "lane": lane.name,
            "model": lane.tag,
            "client": client_name,
            "ticket_id": ticket_id,
            "status": resp.status_code,
            "duration_ms": elapsed_ms,
            "stream": False,
        },
        path=metrics_path,
    )
    out_headers = {
        k: v
        for k, v in resp.headers.items()
        if k.lower() not in ("transfer-encoding", "connection", "content-encoding")
    }
    return Response(content=resp.content, status_code=resp.status_code, headers=out_headers)


async def _stream_proxy(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes,
    *,
    lane: LaneConfig,
    client_name: str,
    ticket_id: str,
    start: float,
    metrics_path: str | None,
    on_release: Callable[[], Coroutine[Any, Any, None]] | None = None,
) -> StreamingResponse:
    async def body_iterator() -> AsyncIterator[bytes]:
        status_code = 502
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(3600.0, connect=30.0)) as stream_client:
                async with stream_client.stream(method, url, headers=headers, content=body) as resp:
                    status_code = resp.status_code
                    async for chunk in resp.aiter_bytes():
                        yield chunk
        finally:
            if on_release:
                try:
                    await on_release()
                except Exception:
                    pass
            elapsed_ms = int((time.time() - start) * 1000)
            log_metric(
                {
                    "event": "proxy_complete",
                    "lane": lane.name,
                    "model": lane.tag,
                    "client": client_name,
                    "ticket_id": ticket_id,
                    "status": status_code,
                    "duration_ms": elapsed_ms,
                    "stream": True,
                },
                path=metrics_path,
            )

    return StreamingResponse(body_iterator(), media_type="application/x-ndjson")
