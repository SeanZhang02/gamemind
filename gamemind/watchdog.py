"""Watchdog — sub-VLM-latency safety checks on raw frames.

Runs every captured frame (<1.1ms budget), no AI model calls. Writes
alerts to Blackboard for Behavior Manager to read. LEVEL 2+ can
directly override Motor via the emergency/freeze protocol.

Four alert levels:
  LEVEL 0 (info):      frame_diff written to blackboard, no action
  LEVEL 1 (warning):   stuck_wall / static_timeout → behavior manager decides
  LEVEL 2 (emergency): screen_red / health_drop → direct motor override (retreat)
  LEVEL 3 (fatal):     screen_black / freeze_timeout → motor frozen, force VLM wake

All checks run on 160x90 downsampled L1 grayscale frames.
"""

from __future__ import annotations

import io
import time
from dataclasses import dataclass
from enum import IntEnum
from typing import Any

from PIL import Image

from gamemind.blackboard import Blackboard, Producer


class AlertLevel(IntEnum):
    INFO = 0
    WARNING = 1
    EMERGENCY = 2
    FATAL = 3


@dataclass
class WatchdogAlert:
    level: AlertLevel
    signal: str
    detail: dict[str, Any]


_DOWNSAMPLE_W = 160
_DOWNSAMPLE_H = 90

_DIFF_STATIC_THRESHOLD = 1.5
_DIFF_SCENE_CHANGE_THRESHOLD = 80.0

_STUCK_WALL_FRAMES = 15
_STUCK_WALL_DIFF_MAX = 2.0

_BLACK_BRIGHTNESS_MAX = 8
_BLACK_CONSECUTIVE_FRAMES = 5

_RED_RATIO_MIN = 0.65
_RED_BRIGHTNESS_MIN = 60
_RED_CONSECUTIVE_FRAMES = 3

_FREEZE_CONSECUTIVE_FRAMES = 90
_FREEZE_DIFF_MAX = 1.0


def _downsample_grayscale(frame_bytes: bytes) -> list[int]:
    img = Image.open(io.BytesIO(frame_bytes))
    img = img.convert("L").resize((_DOWNSAMPLE_W, _DOWNSAMPLE_H), Image.Resampling.NEAREST)
    return list(img.getdata())


def _downsample_rgb(frame_bytes: bytes) -> tuple[list[int], list[int], list[int]]:
    img = Image.open(io.BytesIO(frame_bytes))
    img = img.resize((_DOWNSAMPLE_W, _DOWNSAMPLE_H), Image.Resampling.NEAREST)
    img = img.convert("RGB")
    pixels = list(img.getdata())
    r = [p[0] for p in pixels]
    g = [p[1] for p in pixels]
    b = [p[2] for p in pixels]
    return r, g, b


def _l1_diff(a: list[int], b: list[int]) -> float:
    if len(a) != len(b) or len(a) == 0:
        return 0.0
    return sum(abs(x - y) for x, y in zip(a, b, strict=True)) / len(a)


class Watchdog:
    """Frame-level safety watchdog. Call check() on every captured frame."""

    def __init__(self, blackboard: Blackboard) -> None:
        self._bb = blackboard
        self._prev_gray: list[int] | None = None
        self._low_diff_streak = 0
        self._black_streak = 0
        self._red_streak = 0
        self._freeze_streak = 0
        self._motor_is_moving = False
        self._emergency_override: WatchdogAlert | None = None
        self._freeze_active = False
        self._freeze_start_ns: int = 0
        self._freeze_signal: str = ""

    def set_motor_moving(self, moving: bool) -> None:
        self._motor_is_moving = moving

    def check(self, frame_bytes: bytes) -> list[WatchdogAlert]:
        alerts: list[WatchdogAlert] = []

        # Timed freeze recovery
        if self._freeze_active and self._freeze_start_ns > 0:
            elapsed_ms = (time.monotonic_ns() - self._freeze_start_ns) / 1_000_000.0
            if elapsed_ms >= 10_000:
                # Hard timeout: force recovery regardless of conditions
                self.clear_freeze()
            elif elapsed_ms >= 3_000:
                # Soft recovery: check if the triggering condition has cleared
                # (screen no longer black, or motion detected)
                screen_ok = self._black_streak < _BLACK_CONSECUTIVE_FRAMES
                motion_ok = self._freeze_streak < _FREEZE_CONSECUTIVE_FRAMES
                if screen_ok or motion_ok:
                    self.clear_freeze()

        gray = _downsample_grayscale(frame_bytes)
        r_ch, g_ch, b_ch = _downsample_rgb(frame_bytes)

        diff = _l1_diff(gray, self._prev_gray) if self._prev_gray is not None else 0.0
        self._prev_gray = gray

        self._bb.write("frame_diff_score", diff, Producer.WATCHDOG)

        if diff < _DIFF_STATIC_THRESHOLD:
            self._low_diff_streak += 1
        else:
            self._low_diff_streak = 0

        if (
            self._motor_is_moving
            and self._low_diff_streak >= _STUCK_WALL_FRAMES
            and diff < _STUCK_WALL_DIFF_MAX
        ):
            alerts.append(
                WatchdogAlert(
                    level=AlertLevel.WARNING,
                    signal="stuck_wall",
                    detail={"streak": self._low_diff_streak, "diff": diff},
                )
            )
            self._bb.write("frame_diff_score", -1.0, Producer.WATCHDOG)

        n = len(r_ch)
        if n > 0:
            mean_brightness = sum(r_ch[i] + g_ch[i] + b_ch[i] for i in range(n)) / (n * 3)
        else:
            mean_brightness = 0.0

        if mean_brightness < _BLACK_BRIGHTNESS_MAX:
            self._black_streak += 1
        else:
            self._black_streak = 0

        if self._black_streak >= _BLACK_CONSECUTIVE_FRAMES:
            alerts.append(
                WatchdogAlert(
                    level=AlertLevel.FATAL,
                    signal="screen_black",
                    detail={"streak": self._black_streak, "brightness": mean_brightness},
                )
            )
            if not self._freeze_active:
                self._freeze_start_ns = time.monotonic_ns()
                self._freeze_signal = "screen_black"
            self._freeze_active = True

        if n > 0:
            mean_r = sum(r_ch) / n
            mean_g = sum(g_ch) / n
            mean_b = sum(b_ch) / n
            denom = mean_r + mean_g + mean_b + 1e-6
            red_ratio = mean_r / denom
        else:
            red_ratio = 0.0

        if red_ratio > _RED_RATIO_MIN and mean_brightness > _RED_BRIGHTNESS_MIN:
            self._red_streak += 1
        else:
            self._red_streak = 0

        if self._red_streak >= _RED_CONSECUTIVE_FRAMES:
            alerts.append(
                WatchdogAlert(
                    level=AlertLevel.EMERGENCY,
                    signal="screen_red",
                    detail={"streak": self._red_streak, "red_ratio": red_ratio},
                )
            )
            self._emergency_override = alerts[-1]

        if diff < _FREEZE_DIFF_MAX and not self._motor_is_moving:
            self._freeze_streak += 1
        else:
            self._freeze_streak = 0

        if self._freeze_streak >= _FREEZE_CONSECUTIVE_FRAMES:
            alerts.append(
                WatchdogAlert(
                    level=AlertLevel.FATAL,
                    signal="freeze_timeout",
                    detail={"streak": self._freeze_streak},
                )
            )
            if not self._freeze_active:
                self._freeze_start_ns = time.monotonic_ns()
                self._freeze_signal = "freeze_timeout"
            self._freeze_active = True

        return alerts

    @property
    def is_emergency(self) -> bool:
        return self._emergency_override is not None

    @property
    def is_frozen(self) -> bool:
        return self._freeze_active

    def clear_emergency(self) -> None:
        self._emergency_override = None

    def clear_freeze(self) -> None:
        self._freeze_active = False
        self._freeze_streak = 0
        self._black_streak = 0
        self._freeze_start_ns = 0
        self._freeze_signal = ""

    def reset(self) -> None:
        self._prev_gray = None
        self._low_diff_streak = 0
        self._black_streak = 0
        self._red_streak = 0
        self._freeze_streak = 0
        self._emergency_override = None
        self._freeze_active = False
        self._freeze_start_ns = 0
        self._freeze_signal = ""
