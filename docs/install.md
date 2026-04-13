# GameMind Install Guide

## Prerequisites

Phase C v1 targets Windows only. Mac/Linux developers are not the target
audience for v1 (v2-T3 community trigger may reopen this — see
`docs/final-design.md` §OQ-6). Install requirements:

### Hardware

- **Windows 10 build 1903+** or Windows 11 (for Windows Graphics Capture)
- **NVIDIA GPU with ≥8 GB VRAM** for `gemma4:26b-a4b-it-q4_K_M`
  (Sean's RTX 5090 has 32 GB; 4090 or 3090 works; sub-8GB cards will OOM)
- **~15 GB disk** for Ollama + model weights + Python deps + a few session runs

### Software

- **Python 3.11** via [uv](https://github.com/astral-sh/uv) (not conda, not pyenv)
- **Ollama 0.13+** native Windows install ([ollama.com/download](https://ollama.com/download))
- **git** (any recent version)
- **Anthropic API key** (get one at [console.anthropic.com](https://console.anthropic.com))
- **A game to test with** — Minecraft Java Edition for the primary v1 demo

## 5-minute install (Sean's machine, already set up)

```powershell
# 1. Clone the repo
git clone https://github.com/SeanZhang02/gamemind.git
cd gamemind

# 2. Install deps via uv
uv sync --extra dev

# 3. Set your API key
$env:ANTHROPIC_API_KEY = "sk-ant-..."

# 4. Verify Ollama model is pulled
ollama list | findstr gemma4

# 5. Run doctor
uv run gamemind doctor --all
```

Expected TTHW (time to hello world): ~5 minutes if Ollama + model are pre-pulled, ~20-30 minutes if pulling the 6 GB model for the first time.

## Full install (first-time, cold machine)

```powershell
# 1. Install uv (if you don't have it)
winget install astral-sh.uv

# 2. Install Ollama
winget install ollama.ollama

# 3. Start Ollama in a separate terminal (leave it running)
ollama serve

# 4. Pull the model (large — ~6.1 GB)
ollama pull gemma4:26b-a4b-it-q4_K_M

# 5. Verify
ollama list
# Expected: gemma4:26b-a4b-it-q4_K_M ... 6.1 GB

# 6. Clone GameMind
git clone https://github.com/SeanZhang02/gamemind.git
cd gamemind

# 7. Install Python deps
uv sync --extra dev

# 8. Set your Anthropic API key (per session)
$env:ANTHROPIC_API_KEY = "sk-ant-..."

# Or, for persistent across sessions:
[System.Environment]::SetEnvironmentVariable("ANTHROPIC_API_KEY", "sk-ant-...", "User")

# 9. Verify everything
uv run gamemind doctor --all
```

## What `gamemind doctor --all` checks

Per DX-SUB-2 from the Phase 1 review, `doctor --all` prints the 5 most common failure modes with scripted remediation text:

```
[gamemind doctor] modes: capture, input, live-perception
  remediation table (DX-SUB-2):
    (a) Ollama down       → `ollama serve`
    (b) model not pulled  → `ollama pull gemma4:26b-a4b-it-q4_K_M`
    (c) API key missing   → set ANTHROPIC_API_KEY env var
    (d) no game window    → focus the target game within 10s
    (e) wrong HWND picked → use --window-title filter
```

Phase C Step 1 iteration 6 (this commit range) ships the CLI stub that prints the table. Real doctor sub-checks (capture probe, input loopback, live-perception spike) require Windows + a running game and are wired in follow-up iterations.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | (required) | Your Anthropic Max Plan API key — Amendment A10 requires env var only |
| `GAMEMIND_OLLAMA_HOST` | `http://127.0.0.1:11434` | Ollama HTTP endpoint |
| `GAMEMIND_OLLAMA_MODEL` | `gemma4:26b-a4b-it-q4_K_M` | Ollama model name (Phase C-0 locked) |
| `GAMEMIND_SESSION_TOKEN` | (auto-generated) | Per-launch bearer token for `/v1/*` (Amendment A3) |

## Troubleshooting

### `uv sync` fails with "could not find Python 3.11"

Install Python 3.11 via uv: `uv python install 3.11` then retry `uv sync`.

### `ollama list` doesn't show gemma4

Check that `ollama serve` is running in a separate terminal. Then re-pull: `ollama pull gemma4:26b-a4b-it-q4_K_M`.

### `gamemind doctor` returns "no matching HWND"

The target game isn't running, or the window is minimized. Bring it to focus and retry. If multiple candidate windows are open, use `--window-title "Minecraft*"`.

### Anthropic 401 Unauthorized

`ANTHROPIC_API_KEY` isn't set, or the key has been rotated. Set it:
```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."
```
Verify with `echo $env:ANTHROPIC_API_KEY` (should print the first 12 chars and then a REDACTED suffix per Amendment A10's `scrub_secrets` filter).

### `uv run gamemind daemon start` fails with "port 8766 in use"

Another daemon is already running, OR the PID file is stale. Check:
```powershell
uv run gamemind daemon status
```
If `UP`, stop the other daemon first. If `DOWN` but port 8766 is in use, another process has it — use `netstat -ano | findstr 8766` to find the PID and handle it.

### `/healthz` returns `degraded`

Ollama isn't reachable OR the configured model isn't pulled. Check:
```powershell
curl http://127.0.0.1:11434/api/tags
```
Should return a JSON list including `gemma4:26b-a4b-it-q4_K_M`. If not, `ollama pull gemma4:26b-a4b-it-q4_K_M`.

## What's next

Once `gamemind doctor --all` passes:

1. **Phase C Step 1 remaining work**: real WGC/DXGI bindings, live-perception spike, daemon start wired to uvicorn.run
2. **Phase C Step 2**: input backend (pydirectinput-rgx scan codes)
3. **Phase C Step 3**: adapter loader + brain backend + first chop_logs run on a real Minecraft session

See `docs/final-design.md` §6 for the full Phase C build plan.
