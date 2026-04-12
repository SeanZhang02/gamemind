"""Action Repetition Guard tests per Amendment A13 §1.8.

Primary scenario: "walk into wall" — 6x same hash in 5s, no predicate
fired → guard fires W2 bypass.

Also tests: predicate fired resets guard, different hashes don't trigger,
actions spread over >10s window don't trigger.
"""

from __future__ import annotations

from gamemind.layer2.action_guard import ActionRepetitionGuard

NS = 1_000_000_000


class TestWalkIntoWall:
    """6x same action hash in 5s, no predicate → guard fires."""

    def test_guard_fires_on_6th_repeat(self) -> None:
        guard = ActionRepetitionGuard(ring_size=20, window_s=10.0, max_repeats=5)
        for i in range(5):
            assert not guard.record_action("hash_W", ts_ns=i * NS)
        assert guard.record_action("hash_W", ts_ns=5 * NS)

    def test_guard_does_not_fire_on_5th(self) -> None:
        guard = ActionRepetitionGuard(ring_size=20, window_s=10.0, max_repeats=5)
        for i in range(5):
            result = guard.record_action("hash_W", ts_ns=i * NS)
        assert not result


class TestPredicateResetsGuard:
    """Predicate fired in window → guard does NOT fire even with repeats."""

    def test_predicate_prevents_fire(self) -> None:
        guard = ActionRepetitionGuard(ring_size=20, window_s=10.0, max_repeats=5)
        for i in range(4):
            guard.record_action("hash_W", ts_ns=i * NS)
        guard.mark_predicate_fired(ts_ns=4 * NS)
        assert not guard.record_action("hash_W", ts_ns=4 * NS)
        assert not guard.record_action("hash_W", ts_ns=5 * NS)


class TestDifferentHashes:
    """Distinct actions don't accumulate toward the threshold."""

    def test_mixed_hashes_no_fire(self) -> None:
        guard = ActionRepetitionGuard(ring_size=20, window_s=10.0, max_repeats=5)
        hashes = ["hash_A", "hash_B", "hash_C", "hash_D", "hash_E", "hash_F"]
        for i, h in enumerate(hashes):
            assert not guard.record_action(h, ts_ns=i * NS)


class TestWindowExpiry:
    """Actions spread beyond the window don't count."""

    def test_old_actions_expire(self) -> None:
        guard = ActionRepetitionGuard(ring_size=20, window_s=5.0, max_repeats=5)
        for i in range(3):
            guard.record_action("hash_W", ts_ns=i * NS)
        result = False
        for i in range(3):
            result = guard.record_action("hash_W", ts_ns=(i + 8) * NS)
        assert not result, "old actions at t=0-2 should have expired out of 5s window at t=8-10"


class TestReset:
    def test_reset_clears(self) -> None:
        guard = ActionRepetitionGuard(ring_size=20, window_s=10.0, max_repeats=5)
        for i in range(6):
            guard.record_action("hash_W", ts_ns=i * NS)
        guard.reset()
        assert not guard.record_action("hash_W", ts_ns=10 * NS)
