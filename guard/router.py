"""Model and lane resolution helpers."""

from __future__ import annotations

import json
from typing import Any

from fastapi import Request

from guard.config import ENDPOINT_KEY_ALIASES, GuardConfig, LaneConfig
from guard.queue import validate_model_for_lane


def extract_model_from_body(body: bytes) -> str | None:
    if not body:
        return None
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict):
        model = data.get("model")
        if isinstance(model, str):
            return model
    return None


def lane_hint_from_headers(request: Request) -> str | None:
    for header in ("x-ollama-lane", "x-guard-lane"):
        value = request.headers.get(header)
        if value:
            return ENDPOINT_KEY_ALIASES.get(value.lower(), value.lower())
    client = request.headers.get("x-guard-client")
    return None if not client else None


def resolve_request_lane(
    config: GuardConfig,
    request: Request,
    body: bytes,
    listen_port: int | None,
) -> tuple[LaneConfig | None, str | None, str | None]:
    """
    Returns (lane, model, error_message).
    error_message is set when lane is known but model mismatches pin.
    """
    model = extract_model_from_body(body)
    hint = lane_hint_from_headers(request)
    port = listen_port if listen_port and listen_port != config.agent_port else None

    lane = config.resolve_lane(port=port, lane_hint=hint, model=model)
    if lane is None and port is None and hint:
        lane = config.resolve_lane(lane_hint=hint)

    if lane is None:
        return None, model, "Could not resolve lane from port, header, or model"

    if model and not validate_model_for_lane(lane, model, config.legacy_tags):
        return lane, model, (
            f"Model '{model}' does not match lane pin '{lane.tag}' "
            f"(endpoint {lane.endpoint_key}:{lane.port})"
        )

    return lane, model, None
