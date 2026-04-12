"""DXGIBackend — real DXGI Desktop Duplication via `dxcam`.

Phase C Step 1 Batch A. Replaces the iter-1 stub.

Implementation notes:

- `dxcam` grabs the full desktop and optionally crops to a region via
  `grab(region=(l, t, r, b))`. It's HWND-agnostic — we use
  `GetClientRect` + `ClientToScreen` to translate `hwnd: int` into the
  screen-coordinate rectangle.

- A single `dxcam.DXCamera` is created lazily on first capture and reused
  across calls. Creation is ~20ms but not free.

- dxcam returns (H, W, 3) BGR uint8 numpy arrays. We re-encode as WEBP.

- DXGI fights other Desktop Duplication clients (OBS, Sunshine, Parsec).
  `grab()` returning None is mapped to DXGIFrameGrabError.
"""

from __future__ import annotations

import io
import sys
import time

from gamemind.capture.backend import CaptureBackend, CaptureResult
from gamemind.errors import (
    DXGIFrameGrabError,
    DXGIInitError,
    WindowNotFoundError,
)


class DXGIBackend:
    """Real DXGI Desktop Duplication backend using `dxcam`."""

    def __init__(self, *, probe_on_init: bool = True) -> None:
        self._initialized = False
        self._camera = None
        self._init_error: str | None = None
        if sys.platform != "win32":
            self._init_error = "DXGIBackend requires Windows"
            return
        try:
            import dxcam  # noqa: F401, PLC0415
            import PIL.Image  # noqa: F401, PLC0415
            import numpy  # noqa: F401, PLC0415
        except ImportError as exc:
            self._init_error = f"dxcam / PIL / numpy not importable: {exc}"
            return

        # Health probe: create a camera and try one full-screen grab. If
        # dxcam can't initialize the Desktop Duplication pipeline (e.g.
        # Sunshine / OBS / Parsec is holding the exclusive lock), the
        # StageSurface and DXGIDuplicator will be Initialized:False and
        # grab() returns None. Detect this at __init__ so liveness()
        # reports False cleanly instead of hanging per-call.
        if probe_on_init:
            probe_error = self._health_probe()
            if probe_error is not None:
                self._init_error = probe_error
                return

        self._initialized = True

    def _health_probe(self) -> str | None:
        """Try to create + grab. Returns None on success, error string on failure.

        On failure, also records the reason in self._init_error. Used from
        __init__ to fail fast if another DXGI client is holding the lock.
        """
        import dxcam  # noqa: PLC0415

        try:
            cam = dxcam.create(output_color="BGR")
        except Exception as exc:  # noqa: BLE001
            return f"dxcam.create() raised: {exc}"
        if cam is None:
            return "dxcam.create() returned None — another client holds DXGI?"

        # Check the repr for 'Initialized:True' on the stagesurf + duplicator.
        # dxcam doesn't expose these as programmatic attributes, but their
        # __repr__ strings contain the state ("StageSurface Initialized:True"
        # or "Initalized:False" — note the typo in dxcam).
        stage_repr = repr(getattr(cam, "_stagesurf", ""))
        dup_repr = repr(getattr(cam, "_duplicator", ""))
        if "Initialized:True" not in stage_repr or (
            "Initalized:True" not in dup_repr and "Initialized:True" not in dup_repr
        ):
            try:
                cam.release()
            except Exception:  # noqa: BLE001, S110
                pass
            return (
                f"dxcam StageSurface/Duplicator not initialized "
                f"(likely another DXGI client holds the lock): "
                f"stage={stage_repr} duplicator={dup_repr}"
            )

        # Try one full-screen grab with new_frame_only=False to confirm the
        # pipeline actually produces a frame. Catches the case where the
        # init state looks OK but grab returns None anyway.
        try:
            arr = cam.grab(new_frame_only=False)
        except Exception as exc:  # noqa: BLE001
            try:
                cam.release()
            except Exception:  # noqa: BLE001, S110
                pass
            return f"probe grab raised: {exc}"
        if arr is None:
            try:
                cam.release()
            except Exception:  # noqa: BLE001, S110
                pass
            return "probe grab returned None — DXGI pipeline stale or locked"

        # Probe succeeded — cache the working camera so the first real
        # capture() call doesn't pay create() cost again.
        self._camera = cam
        return None

    def _get_or_create_camera(self):  # noqa: ANN202
        if self._camera is not None:
            return self._camera
        import dxcam  # noqa: PLC0415

        cam = dxcam.create(output_color="BGR")
        if cam is None:
            raise DXGIInitError(
                cause="dxcam.create() returned None — another client may hold exclusive DXGI",
            )
        self._camera = cam
        return cam

    def capture(self, hwnd: int, timeout_ms: int = 1500) -> CaptureResult:  # noqa: ARG002
        if not self._initialized:
            raise DXGIInitError(
                cause=self._init_error or "DXGIBackend not initialized",
                hwnd=hwnd,
            )

        from gamemind.capture._win32 import get_window_rect, is_valid_hwnd  # noqa: PLC0415

        if not is_valid_hwnd(hwnd):
            raise WindowNotFoundError(
                cause=f"HWND {hwnd} is not a valid top-level window",
                hwnd=hwnd,
            )

        try:
            rect = get_window_rect(hwnd)
        except (ValueError, OSError) as exc:
            raise WindowNotFoundError(
                cause=f"GetClientRect failed: {exc}",
                hwnd=hwnd,
            ) from exc

        if rect.width <= 0 or rect.height <= 0:
            raise WindowNotFoundError(
                cause=f"window has zero-area client rect: {rect}",
                hwnd=hwnd,
            )

        capture_ts_monotonic_ns = time.monotonic_ns()
        camera = self._get_or_create_camera()

        # dxcam quirk: default `new_frame_only=True` returns None if the
        # target region hasn't produced a new frame since the last grab.
        # For a synchronous "current frame please" API that's wrong — we
        # want the latest available frame regardless of freshness. Pass
        # new_frame_only=False so static windows still return a frame.
        try:
            arr = camera.grab(region=rect.as_region(), new_frame_only=False)
        except Exception as exc:  # noqa: BLE001
            raise DXGIFrameGrabError(
                cause=f"dxcam.grab() raised: {exc}",
                hwnd=hwnd,
                region=rect.as_region(),
            ) from exc

        if arr is None:
            # Even with new_frame_only=False, dxcam can occasionally return
            # None during the very first grab after create(). Retry once
            # with a small delay before giving up.
            time.sleep(0.05)
            try:
                arr = camera.grab(region=rect.as_region(), new_frame_only=False)
            except Exception as exc:  # noqa: BLE001
                raise DXGIFrameGrabError(
                    cause=f"dxcam.grab() raised on retry: {exc}",
                    hwnd=hwnd,
                    region=rect.as_region(),
                ) from exc

        if arr is None:
            raise DXGIFrameGrabError(
                cause="dxcam.grab() returned None twice (no frame available)",
                hwnd=hwnd,
                region=rect.as_region(),
            )

        frame_age_ms = (time.monotonic_ns() - capture_ts_monotonic_ns) / 1_000_000.0
        variance = _compute_variance(arr)
        height, width = arr.shape[:2]
        frame_bytes = _encode_webp_bgr(arr)

        return CaptureResult(
            frame_bytes=frame_bytes,
            frame_age_ms=frame_age_ms,
            capture_backend="DXGI",
            variance=variance,
            width=int(width),
            height=int(height),
        )

    def liveness(self) -> bool:
        return self._initialized

    def close(self) -> None:
        if self._camera is not None:
            try:
                self._camera.release()
            except Exception:  # noqa: BLE001, S110
                pass
            self._camera = None


def _compute_variance(bgr) -> float:  # noqa: ANN001
    import numpy as np  # noqa: PLC0415

    arr = np.asarray(bgr)
    if arr.ndim == 3 and arr.shape[2] >= 3:
        grey = arr[..., :3].mean(axis=2)
    else:
        grey = arr
    small = grey[::10, ::10]
    small_f = small.astype(np.float32) / 255.0
    return float(np.var(small_f))


def _encode_webp_bgr(bgr) -> bytes:  # noqa: ANN001
    import numpy as np  # noqa: PLC0415
    from PIL import Image  # noqa: PLC0415

    arr = np.asarray(bgr)
    if arr.ndim == 3 and arr.shape[2] == 3:
        rgb = arr[..., [2, 1, 0]].copy()
        img = Image.fromarray(rgb, mode="RGB")
    elif arr.ndim == 3 and arr.shape[2] == 4:
        rgba = arr[..., [2, 1, 0, 3]].copy()
        img = Image.fromarray(rgba, mode="RGBA")
    else:
        img = Image.fromarray(arr)
    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=95)
    return buf.getvalue()


__all__ = ["DXGIBackend", "CaptureBackend", "CaptureResult"]
