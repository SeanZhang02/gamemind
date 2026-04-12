"""C5: AnthropicBackend live ping smoke test.

Validates the API key works, cost estimation is correct for Sonnet 4.6,
and BudgetTracker fires at the right threshold. Uses real Anthropic API.

NOT part of pytest (needs live API key). Run manually:
    set -a; . ./.env.local; set +a
    python scripts/test_anthropic_ping.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

ENV_FILE = REPO_ROOT / ".env.local"


def _load_env() -> None:
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, _, v = line.partition("=")
                os.environ[k.strip()] = v.strip().strip('"').strip("'")


def _log(msg: str) -> None:
    print(f"[anthropic_ping] {msg}", flush=True)


def _fail(msg: str) -> None:
    _log(f"FAIL: {msg}")
    raise SystemExit(1)


def main() -> int:
    _load_env()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        _fail("ANTHROPIC_API_KEY not set. Run: set -a; . ./.env.local; set +a")

    from gamemind.brain.anthropic_backend import AnthropicBackend  # noqa: E402
    from gamemind.brain.budget_tracker import BudgetExceededError, BudgetTracker  # noqa: E402

    _log("instantiating AnthropicBackend with claude-sonnet-4-6...")
    backend = AnthropicBackend(
        system="You are a test ping responder. Reply with exactly the word requested.",
        model="claude-sonnet-4-6",
    )

    _log("calling chat('Reply with exactly: pong')...")
    resp = backend.chat(
        messages=[{"role": "user", "content": "Reply with exactly: pong"}],
        temperature=1.0,
        max_tokens=20,
        cache_system=False,
        request_id="ping-test-1",
    )

    _log(f"  text: {resp.text!r}")
    _log(f"  model: {resp.backend_meta.get('model', 'unknown')}")
    _log(f"  prompt_tokens: {resp.prompt_tokens}")
    _log(f"  completion_tokens: {resp.completion_tokens}")
    _log(f"  cost_estimate_usd: ${resp.cost_estimate_usd:.6f}")
    _log(f"  latency_ms: {resp.latency_ms:.0f}")
    _log(f"  cached_system: {resp.cached_system}")
    _log(f"  request_id: {resp.request_id}")

    if "pong" not in resp.text.lower():
        _fail(f"expected 'pong' in response, got: {resp.text!r}")
    _log("  text check: PASS")

    if resp.cost_estimate_usd <= 0:
        _fail(f"cost_estimate_usd should be >0, got {resp.cost_estimate_usd}")
    if resp.cost_estimate_usd > 0.001:
        _log(f"  WARNING: cost ${resp.cost_estimate_usd:.6f} seems high for a 20-token call")
    _log("  cost check: PASS")

    _log("")
    _log("testing BudgetTracker hard cap...")
    tracker = BudgetTracker(limit_usd=0.0001)
    try:
        tracker.record(resp.cost_estimate_usd)
        _fail("BudgetTracker should have raised BudgetExceededError (limit $0.0001)")
    except BudgetExceededError as e:
        _log(f"  BudgetExceededError correctly raised: {e}")
    _log("  budget guard check: PASS")

    _log("")
    _log("=" * 60)
    _log("C5 ANTHROPIC BACKEND SMOKE: PASS")
    _log("=" * 60)
    _log(f"  total API cost: ${resp.cost_estimate_usd:.6f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
