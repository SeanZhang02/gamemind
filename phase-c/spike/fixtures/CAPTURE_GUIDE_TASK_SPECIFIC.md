# Day 2 Capture Guide — Sean's chop-craft-mine task fixtures

**Purpose**: 10 task-specific Minecraft screenshots for Phase 1 precision/recall gate. These exercise the exact detection classes needed for Sean's live test task (chop wood → craft table → craft pickaxe → mine).

**Resolution**: 1920x1080 preferred (same as existing `phase-c-0/fixtures/t1_*` to keep consistency). F3 debug overlay **OFF**. No mods visible.

**Output**: save all PNGs into `phase-c/spike/fixtures/task/` (a new dir I'll pre-create).

---

## Required fixtures (10 total)

### Tree / wood phase (3 shots)

1. **`tree_intact.png`** — first-person view facing an oak tree at medium distance (~5-8 blocks away). Full trunk visible, leaves visible. Daylight, grass ground visible. No chopping animation.

2. **`tree_midchop.png`** — same tree mid-chopping. Some blocks already broken, chopping particles/cracks visible on the current block. Crosshair aimed at oak_log.

3. **`inventory_with_logs.png`** — E to open inventory AFTER chopping. Hotbar or inventory shows 4+ `oak_log` items (exact count visible). Empty otherwise. No F3.

### Crafting phase (4 shots)

4. **`inventory_2x2_craft_planks.png`** — inventory UI open, oak_log dragged into the 2x2 crafting grid (top-right). Output slot shows `oak_planks` preview. Main grid empty otherwise.

5. **`inventory_with_planks.png`** — inventory UI closed OR hotbar shot showing the resulting `oak_planks` in slot(s) (4+ planks). Nothing else interesting.

6. **`crafting_table_placed_world.png`** — third or first-person world view of a `crafting_table` block placed on grass/stone. Not yet interacted with. Clear unobstructed view, ~2-3 blocks away.

7. **`crafting_table_open_pickaxe.png`** — right-clicked the crafting_table, now 3x3 crafting UI open. Planks arranged in pickaxe recipe: top row 3x planks, middle column 2x sticks. Output slot shows `wooden_pickaxe` preview.

### Pickaxe / mine phase (3 shots)

8. **`inventory_with_pickaxe.png`** — inventory after crafting pickaxe. `wooden_pickaxe` visible in hotbar slot 1 OR inventory. Maybe some remaining planks.

9. **`pickaxe_hotbar_selected.png`** — close UI back to first-person view. Wooden_pickaxe selected in hotbar (visible item in hand at bottom-right). Grass ground visible.

10. **`mining_wood_with_pickaxe.png`** — crosshair aimed at an oak_log (tree), mid-breaking animation (cracks visible on the log). First-person view with pickaxe in hand at bottom-right.

---

## After capturing

1. Save all 10 PNGs into `phase-c/spike/fixtures/task/` (I'll pre-create this directory).
2. Ping me — I'll then:
   - Read each image to propose bbox labels (class + approximate coordinates)
   - Merge with existing `phase-c-0/fixtures/` (20 files)
   - Sean does 5-10 min verification pass on my proposals
   - Run eval: precision/recall at IoU 0.5 per class

## Label classes (Phase 1 vocab, used for GD prompts)

**world objects**: `tree`, `oak_log`, `leaves`, `grass_block`, `stone`, `iron_ore`, `crafting_table`, `cow`, `zombie`, `creeper`

**UI elements**: `inventory_grid`, `crafting_grid_2x2`, `crafting_grid_3x3`, `hotbar`, `inventory_slot`, `item_in_slot`, `output_slot`

**HUD elements**: `health_bar`, `hunger_bar`, `xp_bar`

Not every fixture needs every class — just label what's visible in that fixture.

---

## What to avoid

- F3 debug overlay on (ground-truth should come from your observation, not F3 labels)
- Heavy particle effects obscuring target (unless intentional — e.g. tree_midchop)
- Third-party resource packs (stay vanilla)
- Multiple tasks in one screenshot (keep each fixture focused on ONE phase)
