"""Amendment A1 live-perception spike — 60s Minecraft → Ollama loop.

Validates the Phase C-0 static-fixture PASS generalizes to a continuous
2-3 Hz live stream, per §6 Step 1 acceptance in docs/final-design.md.

Architecture (the core of Amendment A1):

    +--------------+   latest-wins    +---------------+
    | capture loop | ===== slot ====> | inference loop|
    |   ~2-3 Hz    |   bounded size 1 |   as fast as  |
    +--------------+   drops on put   +---------------+

The slot holds AT MOST one frame. If the capture thread produces faster
than the inference thread consumes, the older frame is silently dropped
and the drop counter increments. This is the "latest-wins" semantic
§1.1.A requires: the brain always reasons about the most recent capture,
never a stale one sitting in a FIFO.

Metrics (all recorded per processed tick):
    capture_ts_monotonic      — set just before wgc.capture() call
    inference_end_monotonic   — set right after Ollama /api/chat returns
    end_to_end_ms             — inference_end - capture_ts (this IS the
                                frame_age_at_action gate per Amendment A1)
    inference_latency_ms      — wall-clock of the /api/chat call only
    json_parse_ok             — bool, format=json still may return garbage
    think_leaked              — bool, <think> tag leak detector
    dropped                   — running slot drop counter

Acceptance gates (§6 Step 1, Amendment A1):
    p90 end_to_end_ms          <= 1500 ms   (§6 Step 1)
    p90 frame_age_at_action_ms <= 1000 ms   (Amendment A1)
    backlog_drop_rate          <= 10 %      (§6 Step 1)
    json_parse_rate            >= 95 %      (§6 Step 1)
    think_leak_count           == 0         (regression guard)

Not part of pytest — runs against a live Minecraft window, takes ~60s.
Usage:
    python scripts/test_live_perception_spike.py [--hz 2.0] [--duration 60]
        [--window Minecraft] [--num-ctx 4096]
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import requests  # noqa: E402

from gamemind.capture.wgc_backend import WGCBackend  # noqa: E402
from gamemind.cli import _find_target_hwnd  # noqa: E402

# Locked 2026-04-11 per phase-c-0/C0_CLOSEOUT.md.
OLLAMA_HOST = "http://127.0.0.1:11434"
OLLAMA_MODEL = "gemma4:26b-a4b-it-q4_K_M"

# Mirrors probe/tasks.py::TASKS["t1_block"] exactly. The spike deliberately
# uses T1 because it's the smallest prompt + simplest JSON schema, so a
# regression here cleanly isolates "live stream harder than static" from
# "prompt complexity blew up the budget."
T1_PROMPT = (
    "You are looking at a Minecraft first-person screenshot. "
    "Identify the block type directly in front of the player crosshair "
    "(the center of the screen). Use the canonical Minecraft block id "
    "(e.g. oak_log, stone, grass_block, cobblestone, iron_ore). "
    'If the crosshair is pointing at air or sky, answer "air". '
    "Respond with ONLY valid JSON matching this schema: "
    '{"block": "<block_id>"}'
)


@dataclass
class CapturedFrame:
    frame_bytes: bytes
    capture_ts: float  # monotonic seconds, set BEFORE wgc.capture() call
    width: int
    height: int
    variance: float
    seq: int  # capture sequence number, monotonically increasing


class LatestWinsSlot:
    """Bounded-size-1 latest-wins queue per §1.1.A.

    put() always succeeds; if a frame is already pending, it is overwritten
    and the drop counter increments. take() blocks up to `timeout` for a
    frame to be available, then empties the slot and returns it.
    """

    def __init__(self) -> None:
        self._frame: CapturedFrame | None = None
        self._cond = threading.Condition()
        self._dropped = 0
        self._closed = False

    def put(self, frame: CapturedFrame) -> None:
        with self._cond:
            if self._frame is not None:
                self._dropped += 1
            self._frame = frame
            self._cond.notify_all()

    def take(self, timeout: float = 5.0) -> CapturedFrame | None:
        with self._cond:
            deadline = time.monotonic() + timeout
            while self._frame is None and not self._closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._cond.wait(timeout=remaining)
            if self._closed and self._frame is None:
                return None
            frame = self._frame
            self._frame = None
            return frame

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    @property
    def dropped(self) -> int:
        return self._dropped


@dataclass
class TickMetric:
    seq: int
    capture_ts: float
    inference_end_ts: float
    inference_latency_ms: float
    end_to_end_ms: float  # == frame_age_at_action_ms per Amendment A1
    json_parse_ok: bool
    think_leaked: bool
    parsed_block: str | None
    error: str | None = None


@dataclass
class SpikeResult:
    config: dict
    total_captures: int
    total_processed: int
    total_dropped: int
    backlog_drop_rate: float
    json_parse_rate: float
    think_leak_count: int
    p50_end_to_end_ms: float
    p90_end_to_end_ms: float
    p99_end_to_end_ms: float
    p50_inference_ms: float
    p90_inference_ms: float
    p99_inference_ms: float
    unique_blocks: list[str]
    ticks: list[dict] = field(default_factory=list)


def _log(msg: str) -> None:
    print(f"[a1_spike] {msg}", flush=True)


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = int(round((p / 100.0) * (len(s) - 1)))
    return s[idx]


def _ollama_warmup(num_ctx: int) -> None:
    """Two-phase warmup mirroring probe/client.py::warmup().

    Static vision warmup won't fully match live Minecraft input tokens but
    it primes the KV cache prefill path and vision encoder so the first
    real tick doesn't pay ~2.5s cold-start cost.
    """
    _log("warmup phase 1: text-only load...")
    t0 = time.perf_counter()
    r = requests.post(
        f"{OLLAMA_HOST}/api/chat",
        json={
            "model": OLLAMA_MODEL,
            "messages": [{"role": "user", "content": "Reply with the single word: ok"}],
            "stream": False,
            "options": {"temperature": 0.0, "num_predict": 8},
        },
        timeout=300.0,
    )
    r.raise_for_status()
    _log(f"  text warmup: {(time.perf_counter() - t0) * 1000:.0f}ms")

    from io import BytesIO  # noqa: PLC0415

    from PIL import Image  # noqa: PLC0415

    img = Image.new("RGB", (856, 512), (127, 127, 127))
    buf = BytesIO()
    img.save(buf, format="WEBP", quality=90)
    warmup_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    for i in range(2):
        _log(f"warmup phase 2.{i + 1}: vision warmup...")
        t0 = time.perf_counter()
        r = requests.post(
            f"{OLLAMA_HOST}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": [
                    {"role": "user", "content": T1_PROMPT, "images": [warmup_b64]}
                ],
                "format": "json",
                "stream": False,
                "think": False,
                "options": {"temperature": 0.0, "num_ctx": num_ctx},
            },
            timeout=300.0,
        )
        r.raise_for_status()
        _log(f"  vision warmup {i + 1}: {(time.perf_counter() - t0) * 1000:.0f}ms")


def _call_ollama(
    frame_bytes: bytes, num_ctx: int
) -> tuple[float, str | None, bool, bool, str | None]:
    """One /api/chat call. Returns (latency_ms, block, parse_ok, think_leaked, error)."""
    img_b64 = base64.b64encode(frame_bytes).decode("ascii")
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "user", "content": T1_PROMPT, "images": [img_b64]}
        ],
        "format": "json",
        "stream": False,
        "think": False,
        "options": {"temperature": 0.0, "num_ctx": num_ctx},
    }
    t0 = time.perf_counter()
    try:
        r = requests.post(f"{OLLAMA_HOST}/api/chat", json=payload, timeout=30.0)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        r.raise_for_status()
        data = r.json()
    except requests.RequestException as e:
        latency_ms = (time.perf_counter() - t0) * 1000.0
        return latency_ms, None, False, False, f"http_error: {e}"

    raw = data.get("message", {}).get("content", "")
    think_leaked = "<think>" in raw or "</think>" in raw
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return latency_ms, None, False, think_leaked, "json_parse_failed"
    if not isinstance(parsed, dict):
        return latency_ms, None, False, think_leaked, "json_not_object"
    block = parsed.get("block")
    return latency_ms, str(block) if block is not None else None, True, think_leaked, None


def _capture_loop(
    backend: WGCBackend,
    hwnd: int,
    slot: LatestWinsSlot,
    tick_interval: float,
    stop_event: threading.Event,
    capture_counter: list[int],
) -> None:
    """Captures at ~1/tick_interval Hz until stop_event is set."""
    seq = 0
    next_tick = time.monotonic()
    while not stop_event.is_set():
        now = time.monotonic()
        if now < next_tick:
            time.sleep(min(0.01, next_tick - now))
            continue
        t_capture_start = time.monotonic()
        try:
            result = backend.capture(hwnd=hwnd, timeout_ms=1500)
        except Exception as e:  # noqa: BLE001
            _log(f"  capture seq={seq} FAILED: {type(e).__name__}: {e}")
            next_tick = time.monotonic() + tick_interval
            continue
        frame = CapturedFrame(
            frame_bytes=result.frame_bytes,
            capture_ts=t_capture_start,
            width=result.width,
            height=result.height,
            variance=result.variance,
            seq=seq,
        )
        slot.put(frame)
        seq += 1
        capture_counter[0] = seq
        next_tick += tick_interval
        # If we fell way behind (inference slower than interval), don't
        # machine-gun catch-up — reset schedule to now.
        if time.monotonic() - next_tick > tick_interval * 2:
            next_tick = time.monotonic() + tick_interval


def _inference_loop(
    slot: LatestWinsSlot,
    num_ctx: int,
    stop_event: threading.Event,
    metrics: list[TickMetric],
) -> None:
    """Consumes from slot, runs inference, records metrics until stop_event."""
    while not stop_event.is_set():
        frame = slot.take(timeout=1.0)
        if frame is None:
            continue
        latency_ms, block, ok, leaked, err = _call_ollama(frame.frame_bytes, num_ctx)
        inference_end = time.monotonic()
        end_to_end_ms = (inference_end - frame.capture_ts) * 1000.0
        metric = TickMetric(
            seq=frame.seq,
            capture_ts=frame.capture_ts,
            inference_end_ts=inference_end,
            inference_latency_ms=latency_ms,
            end_to_end_ms=end_to_end_ms,
            json_parse_ok=ok,
            think_leaked=leaked,
            parsed_block=block,
            error=err,
        )
        metrics.append(metric)
        _log(
            f"  tick #{len(metrics):03d} seq={frame.seq:03d} "
            f"e2e={end_to_end_ms:6.0f}ms inf={latency_ms:6.0f}ms "
            f"block={block!r:20s} ok={ok} leak={leaked} "
            f"dropped_so_far={slot.dropped}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hz", type=float, default=2.0, help="Capture tick rate (default 2.0)")
    parser.add_argument(
        "--duration", type=float, default=60.0, help="Spike duration seconds (default 60)"
    )
    parser.add_argument(
        "--window",
        type=str,
        default="Minecraft",
        help="Target window title substring (default 'Minecraft')",
    )
    parser.add_argument(
        "--num-ctx",
        type=int,
        default=4096,
        help="Ollama num_ctx for inference (default 4096, Amendment A15 sweep also 8192)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Path to write JSON report (default runs/a1-spike-<ts>.json)",
    )
    args = parser.parse_args()

    if sys.platform != "win32":
        _log("FAIL: spike only runs on win32 (needs WGC + Minecraft)")
        return 2

    tick_interval = 1.0 / args.hz
    _log(
        f"config: hz={args.hz} interval={tick_interval * 1000:.0f}ms "
        f"duration={args.duration}s window={args.window!r} num_ctx={args.num_ctx}"
    )

    hwnd, title = _find_target_hwnd(args.window)
    if hwnd == 0:
        _log(f"FAIL: no window matching {args.window!r} — make sure Minecraft is open")
        return 1
    _log(f"target HWND={hwnd} title={title!r}")

    backend = WGCBackend()
    if not backend.liveness():
        _log("FAIL: WGCBackend.liveness() False")
        return 1
    _log("WGCBackend OK")

    # Prime the Ollama vision pipeline before the clock starts.
    try:
        _ollama_warmup(args.num_ctx)
    except requests.RequestException as e:
        _log(f"FAIL: Ollama warmup failed — is `ollama serve` running? {e}")
        return 1

    slot = LatestWinsSlot()
    stop_event = threading.Event()
    metrics: list[TickMetric] = []
    capture_counter = [0]

    capture_thread = threading.Thread(
        target=_capture_loop,
        args=(backend, hwnd, slot, tick_interval, stop_event, capture_counter),
        name="a1-spike-capture",
        daemon=True,
    )
    inference_thread = threading.Thread(
        target=_inference_loop,
        args=(slot, args.num_ctx, stop_event, metrics),
        name="a1-spike-inference",
        daemon=True,
    )

    _log("")
    _log("=" * 60)
    _log(f"SPIKE START — running for {args.duration}s")
    _log("=" * 60)
    start_wall = time.monotonic()
    capture_thread.start()
    inference_thread.start()

    try:
        time.sleep(args.duration)
    except KeyboardInterrupt:
        _log("interrupted — stopping early")

    stop_event.set()
    slot.close()
    capture_thread.join(timeout=5.0)
    inference_thread.join(timeout=35.0)  # allow last inference to finish
    total_wall = time.monotonic() - start_wall
    _log(f"SPIKE END — wall={total_wall:.1f}s")
    _log("")

    # ---------- metrics reduction ----------
    total_captures = capture_counter[0]
    total_processed = len(metrics)
    total_dropped = slot.dropped
    if total_captures > 0:
        backlog_drop_rate = total_dropped / total_captures
    else:
        backlog_drop_rate = 0.0
    parse_ok_count = sum(1 for m in metrics if m.json_parse_ok)
    if total_processed > 0:
        json_parse_rate = parse_ok_count / total_processed
    else:
        json_parse_rate = 0.0
    leak_count = sum(1 for m in metrics if m.think_leaked)

    end_to_end_vals = [m.end_to_end_ms for m in metrics]
    inference_vals = [m.inference_latency_ms for m in metrics]
    unique_blocks = sorted({m.parsed_block for m in metrics if m.parsed_block})

    result = SpikeResult(
        config={
            "hz": args.hz,
            "duration": args.duration,
            "window": args.window,
            "num_ctx": args.num_ctx,
            "model": OLLAMA_MODEL,
            "host": OLLAMA_HOST,
            "target_title": title,
            "actual_wall_s": total_wall,
        },
        total_captures=total_captures,
        total_processed=total_processed,
        total_dropped=total_dropped,
        backlog_drop_rate=backlog_drop_rate,
        json_parse_rate=json_parse_rate,
        think_leak_count=leak_count,
        p50_end_to_end_ms=_pct(end_to_end_vals, 50),
        p90_end_to_end_ms=_pct(end_to_end_vals, 90),
        p99_end_to_end_ms=_pct(end_to_end_vals, 99),
        p50_inference_ms=_pct(inference_vals, 50),
        p90_inference_ms=_pct(inference_vals, 90),
        p99_inference_ms=_pct(inference_vals, 99),
        unique_blocks=unique_blocks,
        ticks=[asdict(m) for m in metrics],
    )

    # ---------- report ----------
    _log("=" * 60)
    _log("AMENDMENT A1 LIVE PERCEPTION SPIKE — RESULTS")
    _log("=" * 60)
    _log(f"  captures:    {total_captures}")
    _log(f"  processed:   {total_processed}")
    _log(f"  dropped:     {total_dropped}")
    _log(f"  drop rate:   {backlog_drop_rate * 100:.1f}%  (gate ≤ 10%)")
    _log(f"  parse rate:  {json_parse_rate * 100:.1f}%  (gate ≥ 95%)")
    _log(f"  think leaks: {leak_count}  (gate == 0)")
    _log(f"  e2e p50:     {result.p50_end_to_end_ms:.0f}ms")
    _log(
        f"  e2e p90:     {result.p90_end_to_end_ms:.0f}ms  "
        f"(§6 gate ≤ 1500ms, A1 frame_age gate ≤ 1000ms)"
    )
    _log(f"  e2e p99:     {result.p99_end_to_end_ms:.0f}ms")
    _log(f"  inf p50:     {result.p50_inference_ms:.0f}ms")
    _log(f"  inf p90:     {result.p90_inference_ms:.0f}ms")
    _log(f"  inf p99:     {result.p99_inference_ms:.0f}ms")
    _log(f"  unique blocks seen: {unique_blocks}")
    _log("")

    # ---------- gate check ----------
    gates = {
        "p90_end_to_end_ms ≤ 1500": result.p90_end_to_end_ms <= 1500.0,
        "p90_frame_age_ms ≤ 1000 (A1)": result.p90_end_to_end_ms <= 1000.0,
        "backlog_drop_rate ≤ 10%": backlog_drop_rate <= 0.10,
        "json_parse_rate ≥ 95%": json_parse_rate >= 0.95,
        "think_leak_count == 0": leak_count == 0,
        "total_processed ≥ 30": total_processed >= 30,
    }
    _log("GATES:")
    all_pass = True
    for name, passed in gates.items():
        mark = "PASS" if passed else "FAIL"
        _log(f"  [{mark}] {name}")
        if not passed:
            all_pass = False

    # ---------- persist JSON ----------
    runs_dir = REPO_ROOT / "runs"
    runs_dir.mkdir(exist_ok=True)
    if args.output:
        out_path = Path(args.output)
    else:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        out_path = runs_dir / f"a1-spike-{ts}.json"
    out_path.write_text(
        json.dumps(asdict(result), indent=2, default=str), encoding="utf-8"
    )
    _log(f"report saved: {out_path}")

    _log("")
    if all_pass:
        _log("=" * 60)
        _log("AMENDMENT A1 LIVE PERCEPTION SPIKE: PASS")
        _log("=" * 60)
        return 0
    _log("=" * 60)
    _log("AMENDMENT A1 LIVE PERCEPTION SPIKE: FAIL")
    _log("=" * 60)
    return 1


if __name__ == "__main__":
    sys.exit(main())
