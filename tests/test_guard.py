"""Tests for routing and admission control."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from guard.config import load_config
from guard.queue import AdmissionController, AdmissionDenied, validate_model_for_lane


DEFAULTS = Path(__file__).resolve().parent.parent / "config" / "guard.defaults.yaml"


@pytest.fixture
def config():
    return load_config(defaults_path=DEFAULTS)


@pytest.fixture
def controller(config):
    return AdmissionController(config)


def test_lane_for_port(config):
    assert config.lane_for_port(11436) == "executor"
    assert config.lane_for_port(11435) == "integrator"


def test_lane_for_model(config):
    assert config.lane_for_model("coder-critic") == "executor"
    assert config.lane_for_model("chat-analyzer-92k") == "integrator"
    assert config.lane_for_model("vibethinker:3b") == "serializer"


def test_legacy_tag_routing(config):
    lane = config.resolve_lane(model="qwen2.5-coder:7b-instruct")
    assert lane is not None
    assert lane.endpoint_key == "coder"


def test_validate_model_pin(config):
    lane = config.lanes["executor"]
    assert validate_model_for_lane(lane, "coder-critic", config.legacy_tags)
    assert validate_model_for_lane(lane, "qwen2.5-coder:7b-instruct", config.legacy_tags)
    assert not validate_model_for_lane(lane, "chat-analyzer-92k", config.legacy_tags)


@pytest.mark.asyncio
async def test_acquire_and_release(controller, config):
    lane = config.lanes["executor"]
    ticket = await controller.acquire(lane, model=lane.tag, client="test")
    assert ticket.admitted
    assert controller.total_in_flight() == 1
    await controller.release(ticket)
    assert controller.total_in_flight() == 0


@pytest.mark.asyncio
async def test_drain_blocks_new_work(controller, config):
    controller.set_draining(True)
    lane = config.lanes["executor"]
    with pytest.raises(AdmissionDenied):
        await controller.acquire(lane, model=lane.tag, client="test", timeout=1.0)


@pytest.mark.asyncio
async def test_gpu_group_blocks_sibling(controller, config):
    if "executor_parallel" not in config.lanes:
        pytest.skip("no paracoder lane")
    coder = config.lanes["executor"]
    para = config.lanes["executor_parallel"]
    t1 = await controller.acquire(coder, model=coder.tag, client="a")
    with pytest.raises(AdmissionDenied):
        await controller.acquire(para, model=para.tag, client="b", timeout=0.5)
    await controller.release(t1)
    t2 = await controller.acquire(para, model=para.tag, client="b")
    await controller.release(t2)
