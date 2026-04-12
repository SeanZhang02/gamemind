"""WGCBackend — real Windows Graphics Capture via `windows-capture`.

Phase C Step 1 Batch A. Replaces the iter-1 stub.

Implementation notes:

- `windows-capture` (NiiightmareXD) is callback-based via
  `start_free_threaded()`. Our CaptureBackend Protocol is synchronous, so
  we bridge the two with a threading.Event: start the capture, wait for
  the first frame handler to fire, stop immediately.

- The library matches windows by title string, not HWND. We translate the
  Protocol's `hwnd: int` to a title via ctypes `GetWindowTextW`. If two
  windows share a title, windows-capture picks one — minor v1 limitation.

- Frames arrive as BGRA numpy arrays via `frame.frame_buffer`. We re-encode
  as WEBP via PIL for CaptureResult.frame_bytes.

- Variance is computed on a 10x downsampled greyscale normalized to [0,1]
  to match the selector's VARIANCE_FLOOR check.
"""

from __future__ import annotations

import io
import sys
import threading
import time

from gamemind.capture.backend import CaptureBackend, CaptureResult
from gamemind.errors import WGCInitError, WindowNotFoundError


class WGCBackend:
    """Real Windows Graphics Capture backend using `windows-capture`."""

    def __init__(self) -> None:
        self._initialized = False
        self._init_error: str | None = None
        if sys.platform != "win32":
            self._init_error = "WGCBackend requires Windows"
            return
        try:
            import windows_capture  # noqa: F401, PLC0415
            import PIL.Image  # noqa: F401, PLC0415
            import numpy  # noqa: F401, PLC0415
        except ImportError as exc:
            self._init_error = f"windows_capture / PIL / numpy not importable: {exc}"
            return
        self._initialized = True

    def capture(self, hwnd: int, timeout_ms: int = 1500) -> CaptureResult:
        if not self._initialized:
            raise WGCInitError(
                cause=self._init_error or "WGCBackend not initialized",
                hwnd=hwnd,
            )

        from windows_capture import WindowsCapture  # noqa: PLC0415

        from gamemind.capture._win32 import get_window_title, is_valid_hwnd  # noqa: PLC0415

        if not is_valid_hwnd(hwnd):
            raise WindowNotFoundError(
                cause=f"HWND {hwnd} is not a valid top-level window",
                hwnd=hwnd,
            )

        title = get_window_title(hwnd)
        if not title:
            raise WindowNotFoundError(
                cause=f"HWND {hwnd} has no title; windows-capture requires a title match",
                hwnd=hwnd,
            )

        frame_event = threading.Event()
        captured: dict[str, object] = {}
        capture_ts_monotonic_ns = time.monotonic_ns()

        try:
            wc = WindowsCapture(
                window_name=title,
                draw_border=False,
                cursor_capture=False,
            )
        except Exception as exc:  # noqa: BLE001
            raise WGCInitError(
                cause=f"WindowsCapture ctor failed for title={title!r}: {exc}",
                hwnd=hwnd,
                title=title,
            ) from exc

        @wc.event
        def on_frame_arrived(frame, capture_control):  # noqa: ANN001
            if frame_event.is_set():
                return
            try:
                buf = frame.frame_buffer
                captured["width"] = int(frame.width)
                captured["height"] = int(frame.height)
                captured["bgra"] = buf.copy()
            except Exception as exc:  # noqa: BLE001
                captured["error"] = repr(exc)
            finally:
                frame_event.set()
                try:
                    capture_control.stop()
                except Exception:  # noqa: BLE001, S110
                    pass

        @wc.event
        def on_closed():  # noqa: ANN202
            pass

        try:
            control = wc.start_free_threaded()
        except Exception as exc:  # noqa: BLE001
            raise WGCInitError(
                cause=f"WindowsCapture start failed: {exc}",
                hwnd=hwnd,
                title=title,
            ) from exc

        got_frame = frame_event.wait(timeout=timeout_ms / 1000.0)

        try:
            control.stop()
        except Exception:  # noqa: BLE001, S110
            pass

        if not got_frame:
            raise WGCInitError(
                cause=f"WGC frame did not arrive within {timeout_ms}ms for title={title!r}",
                hwnd=hwnd,
                title=title,
            )

        if "error" in captured:
            raise WGCInitError(
                cause=f"WGC frame handler raised: {captured['error']}",
                hwnd=hwnd,
                title=title,
            )

        bgra = captured["bgra"]
        width = captured["width"]
        height = captured["height"]

        frame_age_ms = (time.monotonic_ns() - capture_ts_monotonic_ns) / 1_000_000.0
        variance = _compute_variance(bgra)
        frame_bytes = _encode_webp(bgra, width, height)  # type: ignore[arg-type]

        return CaptureResult(
            frame_bytes=frame_bytes,
            frame_age_ms=frame_age_ms,
            capture_backend="WGC",
            variance=variance,
            width=width,  # type: ignore[arg-type]
            height=height,  # type: ignore[arg-type]
        )

    def liveness(self) -> bool:
        return self._initialized


def _compute_variance(bgra) -> float:  # noqa: ANN001
    import numpy as np  # noqa: PLC0415

    arr = np.asarray(bgra)
    if arr.ndim == 3 and arr.shape[2] >= 3:
        grey = arr[..., :3].mean(axis=2)
    else:
        grey = arr
    small = grey[::10, ::10]
    small_f = small.astype(np.float32) / 255.0
    return float(np.var(small_f))


def _encode_webp(bgra, width: int, height: int) -> bytes:  # noqa: ANN001, ARG001
    import numpy as np  # noqa: PLC0415
    from PIL import Image  # noqa: PLC0415

    arr = np.asarray(bgra)
    if arr.ndim == 3 and arr.shape[2] == 4:
        rgba = arr[..., [2, 1, 0, 3]].copy()
        img = Image.fromarray(rgba, mode="RGBA")
    elif arr.ndim == 3 and arr.shape[2] == 3:
        rgb = arr[..., [2, 1, 0]].copy()
        img = Image.fromarray(rgb, mode="RGB")
    else:
        img = Image.fromarray(arr)

    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=95)
    return buf.getvalue()


__all__ = ["WGCBackend", "CaptureBackend", "CaptureResult"]
