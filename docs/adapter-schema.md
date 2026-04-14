# Adapter YAML Schema Reference

Game adapters are declarative YAML files under `adapters/`. The loader
(`gamemind/adapter/loader.py`) validates each file against a **strict**
pydantic schema — unknown keys at any nesting level are rejected at load
time. This is the single biggest guard against silent schema drift
(Phase 1 autoplan review, Amendment A8).

> This document annotates `adapters/minecraft.yaml` line-by-line. Use it
> as the canonical reference; the schema source of truth is
> `gamemind/adapter/schema.py`.

## schema_version (required)

```yaml
schema_version: 1
```

Must equal `CURRENT_SCHEMA_VERSION` (currently `1`). Future schema bumps
increment this and require a migration entry in
`gamemind/adapter/migrations/v1_to_v2.py` (not shipped until a breaking
change is needed). **Adapters without this field fail to load.**

## display_name (required)

```yaml
display_name: "Minecraft Java Edition"
```

Human-readable game name. Flows into brain prompts as adapter data
(Rule 3 compliance — the prompt template is generic, the game name
comes through this field). Max length is advisory, not enforced.

## perception (optional)

```yaml
perception:
  freshness_budget_ms: 750.0
  tick_hz: 2.0
```

Per-adapter override for Amendment A1 Perception Freshness Contract.
Defaults:

- `freshness_budget_ms: 750.0` — 2× the nominal 2Hz tick interval.
  Actions computed on a frame older than this are discarded.
- `tick_hz: 2.0` — nominal perception tick rate. The daemon aims for
  this but doesn't enforce it (Layer 1 latency may saturate).

Override when a game needs different timing:

- Turn-based games → higher freshness budget (5000ms or more)
- Twitch reflex games → lower (400-500ms)

## actions (required, non-empty)

```yaml
actions:
  forward: "W"
  backward: "S"
  attack: "MouseLeft"
  open_inventory: "E"
```

Dict mapping logical action names to scan-code key bindings. Keys are
arbitrary adapter-defined names referenced by `goal_grammars[*].preconditions`
and `brain/prompts/templates/*` via `{{ adapter.actions }}`. Values are
scan-code strings per `pydirectinput-rgx` conventions:

- `W`, `A`, `S`, `D`, `E`, etc. — letter keys
- `Space`, `LeftShift`, `LeftControl`, `Tab` — modifier/special keys
- `F1` through `F12` — function keys
- `MouseLeft`, `MouseRight`, `MouseMiddle` — mouse buttons

**At least one action is required** — empty dict fails validation.
**Deterministic ordering** for prompt caching: the assembler sorts
action keys alphabetically at render time, so YAML author ordering
doesn't affect cache hits.

## world_facts (optional, default {})

```yaml
world_facts:
  axe_crafting: "Two planks vertical in a crafting table yield one stick; three planks over two sticks yield an axe."
  log_source: "Oak, birch, spruce, jungle, acacia, dark oak, mangrove, and cherry trees each produce corresponding log blocks when attacked repeatedly."
```

Dict of per-game background facts flowed into brain prompts as
`<adapter-fact>` tags (Amendment A3 Design Rule 4 — treated as data,
never instructions). Use this to give the brain context the vision
model can't extract from a single frame:

- Crafting recipes
- Enemy behaviors
- Hidden mechanics
- Recovery strategies

Keep facts **short** — they're on the cache-hot path. Each fact ~1 sentence.

## inventory_ui (optional, default {})

```yaml
inventory_ui:
  hotbar_slots: 9
  full_inventory_rows: 3
```

Free-form dict for HUD geometry hints. Shape is not strictly validated
in v1 beyond "must be a dict with string keys" — Step 3+ may formalize
it. Common fields:

- `hotbar_slots: int`
- `full_inventory_rows: int`
- `health_bar_region: {x_pct, y_pct, w_pct, h_pct}` (future)

## goal_grammars (required, non-empty)

```yaml
goal_grammars:
  chop_logs:
    description: "Collect N logs of any wood type by attacking tree trunks."
    preconditions:
      - "a log-bearing tree is visible in the current frame"
    success_check:
      predicate:
        type: inventory_count
        target: "log"
        operator: ">="
        value: 3
    abort_conditions:
      - type: health_threshold
        operator: "<"
        value: 0.3
      - type: time_limit
        seconds: 600
```

Dict of named task templates. Each key is a task identifier
(`chop_logs`, `water_crops`, `open_door`). Each value is a `GoalGrammar`:

### GoalGrammar fields

- **`description`** (required): one-line task description. Flows into
  brain prompts as the task statement.
- **`preconditions`** (optional, default `[]`): list of string predicates
  that must hold before the task can start. Currently free-form text;
  Step 3+ may move to a typed predicate grammar.
- **`success_check`** (required): a `SuccessCheck` node. Must set
  **exactly one** of `any_of`, `all_of`, or `predicate`.
- **`abort_conditions`** (optional, default `[]`): list of `AbortCondition`
  entries. ANY fires → session aborts with `outcome: aborted`.

### SuccessCheck composition

```yaml
# Single predicate
success_check:
  predicate:
    type: inventory_count
    target: log
    operator: ">="
    value: 3

# any_of — first satisfied predicate fires success
success_check:
  any_of:
    - predicate:
        type: inventory_count
        target: log
        operator: ">="
        value: 3
    - predicate:
        type: vision_critic
        question: "Is the player inventory full of any wood type?"

# all_of — all predicates must hold simultaneously
success_check:
  all_of:
    - predicate:
        type: health_threshold
        operator: ">"
        value: 0.5
    - predicate:
        type: inventory_count
        target: iron_ingot
        operator: ">="
        value: 5

# Nested any_of → all_of → predicate is supported
```

### Predicate types (tiers per §OQ-4)

| Type | Tier | Fields |
|---|---|---|
| `inventory_count` | 2 (structured vision query) | `target: str`, `operator: str`, `value: int` |
| `template_match` | 1 (cheapest) | `template: str` (path relative to adapter file) |
| `vision_critic` | 3 (Layer 1 freeform yes/no) | `question: str` |
| `health_threshold` | 2 (HUD numeric query) | `operator: str`, `value: float` |
| `time_limit` | — (wall clock) | `seconds: float` |
| `stuck_detector` | — (Layer 2 engine) | (no extra fields; uses Amendment A4 metric) |

### AbortCondition

Similar structure to `Predicate` but scoped to Layer 2 abort triggers:

```yaml
abort_conditions:
  - type: health_threshold
    operator: "<"
    value: 0.3
  - type: time_limit
    seconds: 600
  - type: vision_critic
    question: "Is the player drowning?"
```

## What's NOT in v1 schema

Deferred fields (will bump `schema_version` when added):

- **Memory integration**: persistent per-session state. Step 4+ skill
  library work.
- **Template assets**: binary template images referenced by
  `template_match`. Currently the loader accepts the path string but
  doesn't eagerly load the asset — E123 fires at verify time if
  missing.
- **Per-goal prompt overrides**: goal-specific prompt template keys.
  Deferred until an adapter actually needs it — v1 uses the 5 wake
  templates globally.
- **Savegame fixtures**: `scenarios/<id>/savegame/` references. Step 4
  replay harness work.

## Validation flow

1. Loader resolves path and verifies it's under `adapters/` (Amendment
   A9 path traversal guard).
2. Rejects symlinks at the adapter file level (`is_symlink()` check).
3. `yaml.safe_load()` — blocks `!!python/...` tag injection (Rule 2 +
   E118).
4. Pydantic `Adapter.model_validate(data)` — strict mode, rejects
   unknown keys at any level (E119).
5. Returns a frozen `Adapter` instance on success.

Run `gamemind adapter validate <path>` (Phase C Step 3 scope) to check
without starting a session. Returns a list of human-readable errors,
empty list means valid.
