"""Blackboard tests — 100% coverage of read/write/decay/expiry/swap/consistency."""

from __future__ import annotations

import time

from gamemind.blackboard import Blackboard, Producer, SLOT_CONFIG


def test_write_and_read_basic() -> None:
    bb = Blackboard()
    bb.write("crosshair_block", "oak_log")
    bb.swap()
    result = bb.read("crosshair_block")
    assert result is not None
    assert result.value == "oak_log"
    assert result.confidence > 0.5
    assert not result.expired


def test_read_before_swap_returns_none() -> None:
    bb = Blackboard()
    bb.write("crosshair_block", "oak_log")
    result = bb.read("crosshair_block")
    assert result is None


def test_read_unknown_key_returns_none() -> None:
    bb = Blackboard()
    assert bb.read("nonexistent") is None


def test_write_wrong_producer_ignored() -> None:
    bb = Blackboard()
    bb.write("crosshair_block", "stone", producer=Producer.WATCHDOG)
    bb.swap()
    assert bb.read("crosshair_block") is None


def test_confidence_decays_over_time() -> None:
    bb = Blackboard()
    bb.write("frame_diff_score", 5.0)
    bb.swap()
    r1 = bb.read("frame_diff_score")
    assert r1 is not None
    time.sleep(0.25)
    r2 = bb.read("frame_diff_score")
    assert r2 is not None
    assert r2.confidence < r1.confidence


def test_expiry() -> None:
    bb = Blackboard()
    bb.write("frame_diff_score", 5.0)
    bb.swap()
    time.sleep(0.6)
    result = bb.read("frame_diff_score")
    assert result is not None
    assert result.expired
    assert result.value is None


def test_read_value_with_min_confidence() -> None:
    bb = Blackboard()
    bb.write("crosshair_block", "oak_log")
    bb.swap()
    assert bb.read_value("crosshair_block", min_confidence=0.5) == "oak_log"
    assert bb.read_value("crosshair_block", min_confidence=0.99) is None


def test_double_buffer_isolation() -> None:
    bb = Blackboard()
    bb.write("crosshair_block", "oak_log")
    bb.swap()
    bb.write("crosshair_block", "stone")
    result = bb.read("crosshair_block")
    assert result is not None
    assert result.value == "oak_log"
    bb.swap()
    result = bb.read("crosshair_block")
    assert result is not None
    assert result.value == "stone"


def test_consistency_bonus_3_frames() -> None:
    bb = Blackboard()
    bb.write("crosshair_block", "oak_log")
    bb.write("crosshair_block", "oak_log")
    bb.write("crosshair_block", "oak_log")
    bb.swap()
    result = bb.read("crosshair_block")
    assert result is not None
    base = SLOT_CONFIG["crosshair_block"][3]
    assert result.confidence > base


def test_consistency_penalty_on_change() -> None:
    bb = Blackboard()
    bb.write("crosshair_block", "oak_log")
    bb.write("crosshair_block", "stone")
    bb.swap()
    result = bb.read("crosshair_block")
    assert result is not None
    base = SLOT_CONFIG["crosshair_block"][3]
    assert result.confidence < base


def test_snapshot() -> None:
    bb = Blackboard()
    bb.write("crosshair_block", "oak_log")
    bb.write("health", 0.8)
    bb.swap()
    snap = bb.snapshot()
    assert "crosshair_block" in snap
    assert "health" in snap
    assert snap["crosshair_block"].value == "oak_log"
    assert snap["health"].value == 0.8


def test_clear() -> None:
    bb = Blackboard()
    bb.write("crosshair_block", "oak_log")
    bb.swap()
    bb.clear()
    assert bb.read("crosshair_block") is None


def test_swap_merges_not_replaces() -> None:
    bb = Blackboard()
    bb.write("crosshair_block", "oak_log")
    bb.swap()
    bb.write("health", 0.9)
    bb.swap()
    assert bb.read("crosshair_block") is not None
    assert bb.read("health") is not None


def test_all_slot_configs_valid() -> None:
    for _key, (producer, half_life, expiry, base_conf) in SLOT_CONFIG.items():
        assert isinstance(producer, Producer)
        assert half_life > 0
        assert expiry > 0
        assert 0 < base_conf <= 1.0


def test_confidence_cap_at_095() -> None:
    bb = Blackboard()
    for _ in range(10):
        bb.write("crosshair_block", "oak_log")
    bb.swap()
    result = bb.read("crosshair_block")
    assert result is not None
    assert result.confidence <= 0.95
