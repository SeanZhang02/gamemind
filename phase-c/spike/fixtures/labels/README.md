# Hand-labeling fixtures for Phase 1 spike

Ground truth for the eval harness lives here as labelme-format JSON files —
one `.json` per fixture `.png` in `phase-c/spike/fixtures/task/`. The
harness (`sanity/eval_harness.py`) joins them by filename stem: a fixture
at `fixtures/task/tree1.png` must have labels at
`fixtures/labels/tree1.json`.

## Installing labelme

From the spike venv (`phase-c/spike/.venv`):

```bash
uv pip install labelme
```

`labelme` ships a small Qt GUI. It runs offline; no login required.

## Labeling a fixture

1. Launch the GUI on one image:
   ```bash
   labelme phase-c/spike/fixtures/task/tree1.png
   ```
2. Click **"Create Rectangle"** in the left toolbar (or press `R`).
3. Drag a tight bounding box around one instance of an object. Labelme
   pops a dialog — type the class name exactly as it appears in
   `fixtures/prompts.yaml` (e.g. `tree`, `oak_log`, `inventory_slot`).
   Lowercase snake_case, no spaces.
4. Repeat for every instance visible. Missed objects become false
   negatives in the harness; loose/sloppy boxes lower mean IoU.
5. **File → Save** (or `Ctrl+S`). Labelme writes
   `tree1.json` next to the image by default — **move/copy it** to
   `phase-c/spike/fixtures/labels/tree1.json`.

## JSON format the harness expects

Labelme's native output, only `shape_type: "rectangle"` shapes are read:

```json
{
  "imagePath": "tree1.png",
  "imageWidth": 1920,
  "imageHeight": 1080,
  "shapes": [
    {"label": "tree", "points": [[x1,y1],[x2,y2]], "shape_type": "rectangle"}
  ]
}
```

Polygon / point / line shapes are silently skipped. Two-point rectangles
where point order is (x2,y2) then (x1,y1) work — the harness normalizes.

## Conventions

- Label **every visible instance**, even partial / occluded ones. The
  harness has no "ignore" flag; un-labeled objects in-frame will be
  counted as false positives against the model.
- Use **tight boxes**. The 0.5 IoU gate is unforgiving for loose labels.
- If a class in `prompts.yaml` never appears in any fixture, recall for
  that class is undefined (NaN in the report). That's fine for a spike;
  add a fixture later.
