"""§1.4 W2 stuck detector.

Fires when ALL three conditions hold for `stuck_seconds` (default 20s):
  (a) no predicate_fired event in the window
  (b) motion-quiet every tick (per-pixel L1 diff < entropy_floor)
  (c) no action_executed event

Motion-quiet metric: downsample current frame to 64x64 greyscale, compute
per-pixel absolute L1 diff against the frame captured ~2s ago, normalize
to [0, 1]. Motion-quiet iff metric < entropy_floor (default 0.02).

Uses PIL only (no numpy) so tests run on Linux CI where numpy is not
installed (see pyproject.toml sys_platform marker).
"""

from __future__ import annotations

import io
import time
from collections import deque
from dataclasses import dataclass
from typing import Literal

from PIL import Image


@dataclass
class StuckCheckResult:
    is_stuck: bool
    reason: Literal["motion_quiet_no_predicate_no_action", "guard_bypass"] | None
    metric_value: float


def _decode_grayscale_64(frame_bytes: bytes) -> list[int]:
    img = Image.open(io.BytesIO(frame_bytes))
    img = img.convert("L").resize((64, 64), Image.Resampling.NEAREST)
    return list(img.getdata())


def _motion_metric(a: list[int], b: list[int]) -> float:
    if len(a) != len(b) or len(a) == 0:
        return 1.0
    total = sum(abs(x - y) for x, y in zip(a, b, strict=True))
    return total / (len(a) * 255)


_NOT_STUCK = StuckCheckResult(is_stuck=False, reason=None, metric_value=0.0)


class StuckDetector:
    """Stateful per-session stuck detector.

    Call `update()` on every perception tick. Returns `StuckCheckResult`
    indicating whether the stuck condition holds continuously for the
    configured window.
    """

    def __init__(
        self,
        *,
        stuck_seconds: float = 20.0,
        entropy_floor: float = 0.02,
        lookback_seconds: float = 2.0,
    ) -> None:
        self._stuck_ns = int(stuck_seconds * 1_000_000_000)
        self._entropy_floor = entropy_floor
        self._lookback_ns = int(lookback_seconds * 1_000_000_000)
        self._history: deque[tuple[int, list[int]]] = deque()
        self._quiet_since_ns: int | None = None
        self._predicate_fired_in_window = False
        self._action_executed_in_window = False

    def update(
        self,
        frame_bytes: bytes,
        predicate_fired: bool,
        action_executed: bool,
        ts_ns: int | None = None,
    ) -> StuckCheckResult:
        if ts_ns is None:
            ts_ns = time.monotonic_ns()

        if predicate_fired:
            self._predicate_fired_in_window = True
        if action_executed:
            self._action_executed_in_window = True

        pixels = _decode_grayscale_64(frame_bytes)

        ref = self._find_lookback(ts_ns)
        if ref is not None:
            metric = _motion_metric(pixels, ref)
        else:
            metric = 1.0

        self._history.append((ts_ns, pixels))
        self._prune_history(ts_ns)

        motion_quiet = metric < self._entropy_floor

        if not motion_quiet or self._predicate_fired_in_window or self._action_executed_in_window:
            self._quiet_since_ns = None
            if motion_quiet:
                self._quiet_since_ns = ts_ns
            if not motion_quiet:
                self._predicate_fired_in_window = False
                self._action_executed_in_window = False
            return StuckCheckResult(is_stuck=False, reason=None, metric_value=metric)

        if self._quiet_since_ns is None:
            self._quiet_since_ns = ts_ns

        elapsed_ns = ts_ns - self._quiet_since_ns
        if elapsed_ns >= self._stuck_ns:
            self._quiet_since_ns = None
            self._predicate_fired_in_window = False
            self._action_executed_in_window = False
            return StuckCheckResult(
                is_stuck=True,
                reason="motion_quiet_no_predicate_no_action",
                metric_value=metric,
            )

        return StuckCheckResult(is_stuck=False, reason=None, metric_value=metric)

    def reset(self) -> None:
        self._history.clear()
        self._quiet_since_ns = None
        self._predicate_fired_in_window = False
        self._action_executed_in_window = False

    def _find_lookback(self, now_ns: int) -> list[int] | None:
        target = now_ns - self._lookback_ns
        best: list[int] | None = None
        best_dist = float("inf")
        for ts, pixels in self._history:
            dist = abs(ts - target)
            if dist < best_dist:
                best_dist = dist
                best = pixels
        return best

    def _prune_history(self, now_ns: int) -> None:
        cutoff = now_ns - self._stuck_ns - self._lookback_ns
        while self._history and self._history[0][0] < cutoff:
            self._history.popleft()
