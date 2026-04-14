# Delta Force Capture Guide — Track D baseline fixtures

**Purpose**: 10 三角洲行动 (Delta Force) screenshots to validate the hypothesis "GD zero-shot performs better on photorealistic FPS than on Minecraft pixel-art" before committing Track D.

**Why this matters**: Day 2 Minecraft eval gave GD micro F1=0.152. The architectural pivot toward Track A Hybrid assumes Minecraft's voxel art is the failure mode — not GD itself. Track D is the falsifier. If GD on Delta Force scores >0.4 micro F1, the realism hypothesis holds and a per-game adapter approach is viable. If <0.3, fundamentals need rethinking.

**Honest confidence interval (per investigation report)**: zero-shot GD on Delta Force will land somewhere in **[0.15, 0.70]** F1. That interval is too wide to commit architecture on — we need data.

---

## Capture spec

- **Resolution**: native (1920x1080 or 2560x1440 — whichever you actually play at; consistency matters more than the number)
- **HUD**: ON (this is the realistic deployment state, not a clean view)
- **Anti-cheat**: just play normally, screenshot via Windows `Win+Shift+S` → save as PNG, or game's built-in screenshot
- **Output**: save all 10 PNGs into `phase-c/spike/fixtures_delta/raw/` (I'll pre-create this directory after you say "starting")
- **Naming**: `df01_<scene>.png` through `df10_<scene>.png` per the table below

---

## Required fixtures (10 total)

The fixture set is biased toward the **跑刀 (looting) loop** — that's the actual target task, not combat. Combat shots are included only as negative-class context (the bot needs to *recognize* enemies to *avoid* them, not to fight).

### Movement / navigation phase (3 shots) — bot needs to know "where am I, what's the path"

| # | Filename | Scene | Why |
|---|----------|-------|-----|
| 1 | `df01_corridor_indoor.png` | First-person view inside a building, corridor or room with doors visible. No enemies. | Tests detection of `door`, `stairs`, `wall`, `corridor` — the navigation primitives. |
| 2 | `df02_open_terrain.png` | First-person view outdoors, open field or street. Some buildings 50–200m away. No enemies. | Tests `building`, `road`, `vehicle` (if any) — long-range navigation reference. |
| 3 | `df03_stairwell.png` | First-person view at top or bottom of a stairwell. Stairs clearly visible. | Tests `stairs` specifically — vertical navigation is the hardest motion class. |

### Loot phase (4 shots) — the core 跑刀 detection target

| # | Filename | Scene | Why |
|---|----------|-------|-----|
| 4 | `df04_loot_box_closed.png` | Standing in front of a closed loot container (crate, locker, or similar). Container fills ~20–30% of frame. | Tests `loot_container` zero-shot. Closed state. |
| 5 | `df05_loot_box_open.png` | Same or similar container, opened, items visible inside. Inventory UI may or may not be on screen — your call which is more representative of your bot's typical state. | Tests `loot_item` inside container. The bot needs to decide "is this worth taking". |
| 6 | `df06_inventory_ui.png` | Personal inventory UI fully open. Shows your inventory grid + container grid (if looting), with various items in slots. | Tests `inventory_grid` + `item_in_slot` for FPS — this was the **0/34 worst class** in Minecraft. The realism hypothesis says these should work better here. |
| 7 | `df07_world_loot_pickup.png` | First-person view, crosshair aimed at a loose item on the ground (gun, ammo box, medkit lying outside a container). | Tests `weapon`, `ammo`, `medkit` as world entities (not in UI). |

### Combat / enemy detection phase (3 shots) — the avoid-or-engage classifier

| # | Filename | Scene | Why |
|---|----------|-------|-----|
| 8 | `df08_enemy_distant.png` | First-person view, an enemy player visible at medium distance (30–80m). Enemy in standing/running pose, not prone. | Tests `player_enemy` at distance — the most important detection for "should I keep walking or hide". |
| 9 | `df09_enemy_close.png` | First-person view, enemy player at close range (5–15m). Tactical gear visible. | Tests close-range `player_enemy` — high-stakes detection. The "operator in plate carrier" prompt the investigation report worried about. |
| 10 | `df10_friendly_squad.png` | First-person view with a friendly teammate visible (squad mate, distinguishable nameplate or icon). | Tests `player_friendly` — bot must NOT flee from teammates. Friendly/enemy distinction is the riskiest classifier. |

---

## Class vocabulary (for ground-truth labeling)

After you capture, I'll pre-label these and you do a verification pass (same as Minecraft Day 2 workflow). The vocab I'll propose:

**Player classes** (highest priority — combat avoidance correctness):
- `player_enemy` — hostile player
- `player_friendly` — squad/team player
- `player_body` — generic person if friendly/enemy ambiguous

**Weapons / loot** (跑刀 core):
- `weapon` — any firearm, world or held
- `ammo` — ammo box, magazine, on-ground or in-slot
- `medkit` — health/heal item
- `loot_container` — closed crate/locker/duffel
- `loot_item` — generic interactable item (visible inside open container or world)

**HUD elements** (state reading):
- `health_hud` — health bar / number
- `ammo_count` — ammo counter (current/reserve)
- `crosshair` — center reticle
- `minimap` — corner map
- `compass` — compass bar (top of screen)

**Inventory UI** (reuse Minecraft schema where possible):
- `inventory_grid` — your personal inventory grid
- `loot_grid` — container's grid when looting
- `item_in_slot` — any item occupying a slot (the killer test class)
- `slot_empty` — empty slot

**Navigation entities**:
- `door` — interactable door
- `stairs` — staircase (vertical movement)
- `wall` — major wall surface
- `vehicle` — car / truck / drivable
- `building` — distant building outline

Not every fixture needs every class — just label what's visible.

---

## What to AVOID

- ❌ Replays / spectator mode (we want first-person, in-control state)
- ❌ Death screens, kill cam, loading screens (those are state changes, not perception inputs)
- ❌ Voice chat overlay / Discord overlay / OBS overlay covering significant area
- ❌ Multiple distinct scenes in one shot (one fixture = one scene)
- ❌ Heavy smoke / flashbang / muzzle-flash obscuring 50%+ of frame (unless intentional — but then label it as such)
- ❌ Multiple resolutions across the 10 shots (pick one, stay consistent)

## What's OK to leave in

- ✅ Normal HUD elements (this IS the deployment state)
- ✅ Game-rendered chat / kill feed (those are HUD too; the bot has to deal with them)
- ✅ Crosshair, compass, minimap (label them so we measure)
- ✅ Some motion blur (real gameplay has it)

---

## After you capture

1. Save all 10 PNGs into `phase-c/spike/fixtures_delta/raw/`
2. Tell me "Delta Force fixtures ready" — I'll then:
   - Pre-label each via multimodal Read (propose bbox + class)
   - Save proposals as labelme JSON in `phase-c/spike/fixtures_delta/labels/`
   - You do a 5–10 min verification pass (same as Minecraft, Sean caught 1 ground-truth flip last time — your eye is more reliable than mine on game-domain detail)
   - I run `eval_harness.py` adapted for Delta Force vocab → micro F1 vs GD-Minecraft 0.152
3. Decision gate: F1 > 0.4 → Track D viable, write Delta Force adapter; F1 < 0.3 → fundamentals need rethinking before any per-game adapter

## Scope this guide does NOT cover

- Anti-cheat detection of screenshot tooling (assume normal Win+Shift+S is invisible to AC)
- Multi-frame / temporal fixtures (we're testing single-frame zero-shot only — temporal fusion is a Phase 2 concern)
- Audio cues (bot would also use audio in deployment, but perception eval is visual-only here)

---

**Estimated capture time**: 15–20 minutes of normal play if you can stage scenes, longer if you want truly natural shots. Quality > quantity — one good `df09_enemy_close.png` beats five mediocre ones.
