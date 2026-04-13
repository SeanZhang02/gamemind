"""Tests for gamemind.spatial.state — SpatialState double-buffered world model."""

from __future__ import annotations

import time


from gamemind.spatial.state import SpatialPerception, SpatialState


def _perception(**kwargs) -> SpatialPerception:
    """Helper to build a SpatialPerception with sensible defaults."""
    defaults = dict(
        block=None,
        facing=None,
        spatial_context=None,
        anchors=None,
        health=None,
        entities=None,
    )
    defaults.update(kwargs)
    return SpatialPerception(**defaults)


# ---------------------------------------------------------------------------
# update() tests
# ---------------------------------------------------------------------------


class TestUpdateParsesFacing:
    def test_update_parses_facing(self):
        ss = SpatialState()
        ss.update(_perception(facing="looking_down"))
        ss.swap()
        snap = ss.snapshot()
        assert "looking down" in snap


class TestUpdateRejectsInvalidFacing:
    def test_update_rejects_invalid_facing(self):
        ss = SpatialState()
        # Set a valid facing first
        ss.update(_perception(facing="looking_down"))
        # Now send an invalid facing — should keep previous
        ss.update(_perception(facing="upside_down"))
        ss.swap()
        snap = ss.snapshot()
        assert "looking down" in snap
        assert "upside_down" not in snap


class TestUpdateStoresBlock:
    def test_update_stores_block(self):
        ss = SpatialState()
        ss.update(_perception(block="oak_log"))
        ss.swap()
        snap = ss.snapshot()
        assert "oak_log" in snap


class TestUpdateStoresSpatialContext:
    def test_update_stores_spatial_context(self):
        ss = SpatialState()
        ss.update(_perception(spatial_context="Dense forest with tall oaks"))
        ss.swap()
        snap = ss.snapshot()
        assert "Dense forest with tall oaks" in snap


class TestUpdateCreatesAnchor:
    def test_update_creates_anchor(self):
        ss = SpatialState()
        ss.update(
            _perception(
                anchors=[
                    {"label": "oak_tree", "direction": "ahead_right", "distance": "close"},
                ]
            )
        )
        ss.swap()
        snap = ss.snapshot()
        assert "oak_tree" in snap
        assert "ahead_right" in snap
        assert "close" in snap


class TestUpdateRefreshesExistingAnchor:
    def test_update_refreshes_existing_anchor(self):
        ss = SpatialState()
        anchor = {"label": "oak_tree", "direction": "ahead", "distance": "far"}
        ss.update(_perception(anchors=[anchor]))

        # Get the first timestamp via the back buffer (before swap)
        with ss._lock:
            first_ts = ss._back.anchors["oak_tree"].last_seen_ns

        # Small delay to ensure monotonic_ns advances
        time.sleep(0.001)

        # Re-observe same label with updated direction/distance
        anchor2 = {"label": "oak_tree", "direction": "ahead_right", "distance": "close"}
        ss.update(_perception(anchors=[anchor2]))

        with ss._lock:
            refreshed = ss._back.anchors["oak_tree"]
            assert refreshed.last_seen_ns > first_ts
            assert refreshed.direction == "ahead_right"
            assert refreshed.distance == "close"


class TestUpdateRejectsInvalidAnchorDirection:
    def test_update_rejects_invalid_anchor_direction(self):
        ss = SpatialState()
        ss.update(
            _perception(
                anchors=[
                    {"label": "bad_tree", "direction": "above", "distance": "close"},
                ]
            )
        )
        ss.swap()
        snap = ss.snapshot()
        assert "bad_tree" not in snap


class TestUpdatePrunesExpiredAnchors:
    def test_update_prunes_expired_anchors(self):
        # Use a very short max age so we can test pruning
        ss = SpatialState(anchor_max_age_ns=1)  # 1 nanosecond
        anchor = {"label": "old_tree", "direction": "ahead", "distance": "far"}
        ss.update(_perception(anchors=[anchor]))

        # Small sleep to ensure the anchor is older than 1 ns
        time.sleep(0.001)

        # Next update triggers pruning (no new anchors needed)
        ss.update(_perception())
        ss.swap()
        snap = ss.snapshot()
        assert "old_tree" not in snap


# ---------------------------------------------------------------------------
# swap() tests
# ---------------------------------------------------------------------------


class TestSwapPromotesBackToFront:
    def test_swap_promotes_back_to_front(self):
        ss = SpatialState()
        ss.update(_perception(block="diamond_ore", facing="looking_up"))
        ss.swap()
        snap = ss.snapshot()
        assert "diamond_ore" in snap
        assert "looking up" in snap


class TestSwapIsolation:
    def test_swap_isolation(self):
        ss = SpatialState()
        # Write initial data and swap
        ss.update(_perception(block="stone"))
        ss.swap()

        # Write new data but DON'T swap — front should still show "stone"
        ss.update(_perception(block="dirt"))
        snap = ss.snapshot()
        assert "stone" in snap
        assert "dirt" not in snap


# ---------------------------------------------------------------------------
# snapshot() tests
# ---------------------------------------------------------------------------


class TestSnapshotReturnsText:
    def test_snapshot_returns_text(self):
        ss = SpatialState()
        ss.update(
            _perception(
                facing="looking_at_horizon",
                block="grass_block",
                anchors=[
                    {"label": "cow", "direction": "ahead", "distance": "medium"},
                ],
                health=0.8,
                entities=["zombie"],
            )
        )
        ss.swap()
        snap = ss.snapshot()
        assert "Camera: looking at horizon." in snap
        assert "Crosshair on: grass_block." in snap
        assert "cow (ahead, medium)" in snap
        assert "Health: 0.8." in snap
        assert "Entities: zombie." in snap


class TestSnapshotCapsAt20Anchors:
    def test_snapshot_caps_at_20_anchors(self):
        ss = SpatialState()
        anchors = [
            {"label": f"tree_{i}", "direction": "ahead", "distance": "close"} for i in range(30)
        ]
        ss.update(_perception(anchors=anchors))
        ss.swap()
        snap = ss.snapshot()
        # Count how many "tree_" labels appear
        count = sum(1 for i in range(30) if f"tree_{i}" in snap)
        assert count == 20


class TestSnapshotEmptyShowsSentinel:
    def test_snapshot_empty_shows_sentinel(self):
        ss = SpatialState()
        ss.swap()
        snap = ss.snapshot()
        assert "No notable objects visible." in snap


# ---------------------------------------------------------------------------
# diff() tests
# ---------------------------------------------------------------------------


class TestDiffDetectsFacingChange:
    def test_diff_detects_facing_change(self):
        ss = SpatialState()
        ss.update(_perception(facing="looking_down"))
        ss.update(_perception(facing="looking_at_horizon"))
        ss.swap()
        d = ss.diff()
        assert d is not None
        assert "Camera changed to looking at horizon" in d


class TestDiffDetectsBlockChange:
    def test_diff_detects_block_change(self):
        ss = SpatialState()
        ss.update(_perception(block="oak_log"))
        ss.update(_perception(block="air"))
        ss.swap()
        d = ss.diff()
        assert d is not None
        assert "Block changed from oak_log to air" in d


class TestDiffReturnsNoneWhenNoChange:
    def test_diff_returns_none_when_no_change(self):
        ss = SpatialState()
        p = _perception(facing="looking_down", block="stone")
        ss.update(p)
        ss.update(p)
        ss.swap()
        d = ss.diff()
        assert d is None
