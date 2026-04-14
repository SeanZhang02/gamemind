# Research: Grounding DINO variant selection for GameMind Phase 1 spike

**Date**: 2026-04-13
**Author**: ML infra research pass (pre-spike, no code)
**Status**: Research only. No empirical numbers collected on our hardware yet.
**Target hardware**: Windows 11 + Python 3.11 + RTX 5090 32GB (Blackwell, sm_120) + CUDA 12.8

---

## TL;DR

Recommend **`IDEA-Research/grounding-dino-tiny`** (HF transformers integration) as the Phase 1 default, with `owlv2-base-patch16-ensemble` kept as a fast-swap fallback. The tiny variant is the pragmatic choice because:

1. There is **no public zero-shot benchmark on Minecraft or voxel/pixel-art imagery** for any of these models — tiny vs base accuracy gap on the narrow prompt set (`tree`, `cow`, `oak_log`, `inventory slot`) is an empirical unknown, not a known quantity.
2. Tiny's ~2GB VRAM and faster inference leaves headroom for the Layer-2 Qwen3-VL on the same 32GB card; base gives us a marginal COCO mAP bump that may or may not translate to Minecraft.
3. HF transformers integration sidesteps the CUDA ops compilation patch that the official IDEA repo requires on Blackwell (documented Linux pain; Windows is untested).
4. Florence-2 has an `<OPEN_VOCABULARY_DETECTION>` task token but no public benchmarks against GD/OWLv2 on game content — parked as third option.

**Hard unknowns requiring spike validation**: (a) zero-shot recall on Minecraft's low-res pixelated textures, (b) whether HF transformers pre-built wheels work on sm_120 without source build, (c) actual per-frame latency on 5090 at our input resolution, (d) Supervision library behavior on Windows (not Linux).

**Confidence score: 6/10** — evidence supports the *decision framework* (tiny-first, keep escape hatch), but the actual Minecraft accuracy and Blackwell compatibility numbers are unverified.

---

## Q1: Grounding DINO variants + alternatives — which is best for non-photographic voxel/pixel art?

### Published numbers (photographic benchmarks — COCO/LVIS)

| Model | Params | VRAM (fp32 est.) | COCO zero-shot AP | Weights public? |
|---|---|---|---|---|
| `IDEA-Research/grounding-dino-tiny` (Swin-T backbone) | 172M | ~2GB | 48.4 (per paper's Swin-T config) | Yes (HF) |
| `IDEA-Research/grounding-dino-base` (Swin-B backbone) | 340M | ~4GB | 52.5 | Yes (HF) |
| Grounding DINO 1.5 Pro | — | — | 54.3 COCO / 55.7 LVIS-minival | **API only, no weights** |
| `google/owlv2-base-patch16-ensemble` | ~154M | ~1.5GB | ~47.0 mAP on similar tasks | Yes (HF) |
| `microsoft/Florence-2-base` | 232M | ~2GB | Not directly comparable (unified VLM) | Yes (HF) |

**Source candidness**: The "COCO zero-shot" numbers above are from the original GD paper and a Towards AI practitioner benchmark on a **20-image toy-car subset**. They are photographic images. They do not tell us anything useful about Minecraft.

### Evidence on non-photographic / voxel / pixel-art imagery

**Searched queries**:
- "Grounding DINO Minecraft zero-shot object detection"
- "OWLv2 game screenshot zero-shot detection benchmark"
- "zero-shot object detection voxel pixel art non-photographic"
- "Florence-2 open vocabulary detection game UI icon"

**Finding**: Zero direct public benchmarks for any of these models on Minecraft, other voxel games, or pixel art specifically. The surveys that do exist (Roboflow comparison page, Towards AI benchmark, inteligenai 2025 guide) exclusively evaluate on photographic domains (COCO, LVIS, toy cars, muzzle/cattle detection, eyewear).

**Closest analogues found**:
- Community YOLO Minecraft datasets on Roboflow Universe (e.g., "minecraft-ogpjp" 1360 animal images, "minecraft-mob-detection" 33 classes, "minecraft-tree-detector" 238 images) — these are **fine-tuned YOLOv8** projects, not zero-shot. Their existence confirms (a) the domain is tractable with supervised training, (b) nobody published zero-shot results on them.
- MineCLIP / MineDojo / STEVE-1 / VPT / JARVIS-1 all operate on Minecraft pixels but as action-policy / reward models, not bounding-box detectors. None is a drop-in for our "emit bboxes for `tree`, `cow`, `oak_log`" requirement.

**Honest assessment**: Grounding DINO's training data (GoldG + Cap4M + OI + O365 + RefCOCO) is photographic. Minecraft textures are 16×16 pixel-art tiles upscaled, with flat shading, no depth cues, blocky silhouettes. There is a real risk that:
- `tree` zero-shot works reasonably (silhouette cue is strong)
- `cow` zero-shot works passably (distinctive black/white pattern even in Minecraft)
- `oak_log` (material distinction) is **shaky** — requires discriminating log variants
- `inventory slot with item` is **the highest-risk prompt** — UI chrome is out of distribution for every VLM detector

This is untested territory. Do not trust confidence numbers anyone quotes here.

### Why tiny over base for first spike

If zero-shot on Minecraft fails, going from tiny → base is unlikely to rescue it — the domain gap is the issue, not model capacity. If tiny works, base is a small upgrade at 2× VRAM. Prefer the small-footprint variant to keep headroom for Layer-2 VLM.

### Why GD-tiny over OWLv2-base as primary

- GD was designed for text-grounded detection with referring expressions (useful for `inventory slot with item` compositionality). OWLv2 tends to shine on clean precision but less on compositional prompts.
- GD has more HF community integration with Supervision.
- Keep OWLv2 as 30-minute-swap alternative if GD fails.

---

## Q2: Minecraft-specific evidence

**Direct zero-shot open-vocab detection on Minecraft**: no published evidence found.

What exists:
- **Supervised YOLO fine-tunes** on Minecraft screenshots (Roboflow Universe, Kaggle notebooks) — demonstrates that with ~1000 labeled images and YOLOv8, Minecraft mobs/trees are detectable at high accuracy. Not zero-shot.
- **VLM-based agents** (MineCLIP, STEVE-1, JARVIS-1, JARVIS-VLA, Optimus-2 CVPR 2025, MineStudio) — these produce action distributions or reward scores, not bounding boxes. JARVIS-VLA (2025) post-trains a VLM for keyboard/mouse output; not a detector.
- **Kennesaw State undergrad project (2023)** — "Machine Learning in Minecraft: Proof of Concept for Object Detection Oriented Autonomous Bots" — uses YOLOv8 with 512×288 sampled frames, supervised.

**This is untested territory for zero-shot open-vocab**. Flag it explicitly. Our spike will likely be the first public data point.

**Implication for spike design**: the spike MUST include a small hand-labeled Minecraft validation set (≥ 20 frames, ≥ 3 classes) before declaring GD "works". Do not rely on subjective visual inspection.

---

## Q3: Roboflow Supervision on Windows 11 + Python 3.11 + CUDA 12

### Documented Supervision / ByteTrack issues (cross-platform)

From scanning `roboflow/supervision` GitHub issues:
- **#1164 ByteTrack memory leak**: removed_tracks list grows unbounded; over long sessions (which GameMind will have) memory creeps up. Mitigation: periodic tracker reset. **Relevant to our long-running game sessions.**
- **#1408 / #1215 / #1582 / #1670**: various ByteTrack detection-loss / threshold-inversion / class_id requirement bugs. Some resolved in later releases; check changelog.
- **#418**: segmentation mask loss when passing through ByteTracker. Not critical for GameMind Phase 1 (we use bboxes).

### Windows-specific findings

**No Windows-11-specific ByteTrack issues surfaced** in the tracker. Supervision is pure-Python (no Windows-hostile C extensions in its own code), so the main Windows risks come from **transitive deps**:
- `lap` / `lapx` (linear assignment for tracking) — historically required MSVC build tools on Windows pre-built wheels. Recent versions ship Windows wheels but Python 3.11 + 3.12 coverage varies.
- `pycocotools` — similar Windows wheel story.
- Supervision's own install uses pip wheels; we should prefer `pip install supervision` over source install on Windows.

**Action**: During spike env setup, pin `supervision>=0.25` (recent stable) and verify `pip install supervision` pulls Windows wheels for all transitive deps without asking for a C compiler. If it does demand a compiler, fall back to conda-forge for `lap`.

### Long-session memory caveat

Explicit note for GameMind: ByteTrack's unbounded `removed_tracks` list (#1164) means if the agent runs for hours, tracker memory grows. Design consideration, not a blocker.

---

## Q4: RTX 5090 / Blackwell (sm_120) / CUDA 12 compatibility

### PyTorch state as of April 2026

- **Stable PyTorch builds shipped through early 2026 were compiled up to sm_90 (Hopper)**. Running on sm_120 throws "no kernel image available" or silently CPU-fallbacks.
- **CUDA 12.8+ toolkits support sm_120** on the NVIDIA side.
- **Working combinations documented**:
  - PyTorch **nightly** targeting CUDA 12.8 — works on 5090. Command pattern: `pip install --pre torch torchvision --index-url https://download.pytorch.org/whl/nightly/cu128`
  - PyTorch **2.12 built from source** with `TORCH_CUDA_ARCH_LIST="12.0"` and CUDA 12.9 — documented by March 2026 community guide.
  - Nightlies targeting cu124 with GCC 14 — used by the Medium "Running Grounded-SAM-2, GroundingDINO, PointNet++ on RTX 5090" article (Linux only).
- **pytorch/pytorch#164342** is the tracking issue for official sm_120 in stable builds — still open in early 2026.

### Transformers / Grounding DINO on sm_120

The official `IDEA-Research/GroundingDINO` repo has **CUDA C++ extensions** (custom ops) that must be compiled against our CUDA toolkit. The known fix on 5090 Linux: patch `setup.py` to add `-gencode=arch=compute_120,code=sm_120`. This is the major pain point.

**However**: the **HuggingFace transformers port** (`transformers.GroundingDinoForObjectDetection`) reimplements the model in pure PyTorch without the custom CUDA kernels. If we use the HF port (not the original IDEA repo), **we avoid the setup.py compilation step entirely**. This is the single biggest reason to prefer the HF integration on Windows + Blackwell.

There is a documented latency gap: HF's GroundingDINO implementation was measured at ~378–528 ms/image (issue #31533), vs the original IDEA repo reaching ~100 ms. That is a **3–5× slowdown** on the HF version. For a Minecraft agent running at 2–5 FPS perception cadence, the HF tiny model should still fit, but the budget is tight.

### Windows specifics

Every working 5090 + GroundingDINO recipe I found is **Linux**. Windows on Blackwell is functionally untested for this stack. **Biggest single risk for Phase 1 setup**.

**Mitigations to try in spike (in order)**:
1. `pip install --pre torch torchvision --index-url https://download.pytorch.org/whl/nightly/cu128` on Windows native — if wheels exist, fastest path.
2. If #1 fails: WSL2 Ubuntu 22.04, follow the March 2026 Medium guide. Loses Windows-native DirectX game capture convenience.
3. If both fail: defer Blackwell, run on whatever older CUDA-compatible GPU is available, and flag the deployment story for re-architecture.

---

## Q5: Recommended configuration

### Model

- **Primary**: `IDEA-Research/grounding-dino-tiny` via HuggingFace `transformers.AutoModelForZeroShotObjectDetection`
- Pin to a revision hash (avoid silent model updates). From HF tree, most recent commits on `main` as of search: processor commit `e08274d3760f8fcfc53dcbb9ca3ed0a29fa9c40e` (Dec 2023) is a known-stable processor state. Verify latest `main` commit at spike kickoff and pin explicitly: `from_pretrained("IDEA-Research/grounding-dino-tiny", revision="<hash>")`.
- **Fallback A (swap in ~30 min)**: `google/owlv2-base-patch16-ensemble`
- **Fallback B (research-only)**: `microsoft/Florence-2-base` with `<OPEN_VOCABULARY_DETECTION>` task token

### Version pins (to verify in spike)

```
# Primary attempt (Windows native, hopeful)
torch>=2.5.0+cu128  (nightly or stable once sm_120 lands)
torchvision (matching)
transformers>=4.47  (has mature GD integration, fast image processor)
supervision>=0.25
numpy<2.0  (supervision compatibility warning)
pillow>=10
```

If nightly wheel is not available for Python 3.11 Windows cu128, fall back to WSL2.

### Expected inference latency (pre-spike estimate — VERIFY)

- HF GD-tiny reported baseline on unspecified GPU: ~380–530 ms/frame.
- On 5090, optimistic 3–5× over that reference GPU (depends heavily on what reference was — assume T4-class): could land at **~80–150 ms/frame** at 640×640 input.
- Grounding DINO 1.5 Edge on Orin NX hits 10 FPS at 640×640 — suggests the architecture is tractable for real-time; 5090 should easily exceed this.
- **Budget target for GameMind Layer 1**: ≤ 200 ms/frame → comfortable for 5 FPS perception cadence. Should fit but verify empirically.

**These are estimates, not measured.** The spike's first deliverable after env setup should be a simple latency benchmark (100 frames, warmup, report P50/P95).

### Unknowns that MUST be verified during the spike

1. **Does `torch` with sm_120 support install cleanly on Windows native via pip?** (Binary install success/failure.)
2. **Does `transformers` GroundingDINO forward pass work end-to-end on sm_120** without CUDA kernel errors? (Functional correctness.)
3. **Zero-shot detection quality on Minecraft for each prompt**: `tree`, `cow`, `oak_log`, `inventory slot with item`. Needs hand-labeled 20+ frame validation set with precision/recall at IoU 0.5. (Domain fit.)
4. **Per-frame latency P50/P95** at our actual input resolution (Minecraft window size — likely 1920×1080 downsampled to 800×800 or 640×640). (Performance budget.)
5. **Supervision + ByteTrack on Windows install clean** (no C compiler required). (Tooling survivability.)
6. **UI element detection failure mode**: does GD output anything sensible for `inventory slot`, or does it produce random garbage? This is the Layer-1 → Layer-2 handoff assumption. If GD cannot localize UI at all, the whole two-layer architecture needs revisiting before Phase 2.
7. **VRAM footprint under real load**: model + CUDA context + Minecraft running on same box. Budget assumes ~2GB for GD-tiny; Minecraft itself can use 4–8GB if GPU-accelerated rendering. Verify combined headroom leaves room for Qwen3-VL 8B q4_K_M (~6GB).
8. **Long-session memory stability**: ByteTrack #1164 leak over a 1-hour session.

### Kill-switch criteria (when to abandon GD and try alternatives)

If after 2 spike days:
- GD-tiny zero-shot precision on `tree`/`cow` < 0.5 @ IoU 0.5: domain gap is fatal → try OWLv2, then Florence-2, then consider "fine-tune a 500-image YOLOv8 on Minecraft" as architectural pivot.
- Latency > 500 ms/frame on 5090: architecture mismatch → try OWLv2 (often faster), or move to GD-1.5-Edge (requires API access).
- Windows install fails and WSL2 has GPU passthrough issues: descope Phase 1 to Linux-only dev environment, defer Windows support.

---

## Sources

**Grounding DINO core**:
- [Grounding DINO: Marrying DINO with Grounded Pre-Training (arxiv)](https://arxiv.org/html/2303.05499v5)
- [IDEA-Research/GroundingDINO GitHub](https://github.com/IDEA-Research/GroundingDINO)
- [IDEA-Research/grounding-dino-tiny on HF](https://huggingface.co/IDEA-Research/grounding-dino-tiny)
- [IDEA-Research/grounding-dino-base on HF](https://huggingface.co/IDEA-Research/grounding-dino-base)
- [HF transformers Grounding DINO docs](https://huggingface.co/docs/transformers/en/model_doc/grounding-dino)
- [Roboflow Grounding DINO SOTA blog](https://blog.roboflow.com/grounding-dino-zero-shot-object-detection/)
- [Ikomia Grounding DINO explainer](https://www.ikomia.ai/blog/grounding-dino-zero-shot-detection-explained)

**Zero-shot detection comparisons / benchmarks**:
- [Towards AI benchmarking SOTA zero-shot detection](https://towardsai.net/p/machine-learning/benchmarking-zero-shot-object-detection-a-practical-comparison-of-sota-models)
- [Roboflow GD vs OWLv2 compare page](https://roboflow.com/compare/grounding-dino-vs-owlv2)
- [Roboflow top zero-shot detection models](https://roboflow.com/model-feature/zero-shot-detection)
- [inteligenai 2025 enterprise zero-shot detection guide](https://inteligenai.com/zero-shot-detection-enterprise/)
- [OWLv2 HF docs](https://huggingface.co/docs/transformers/main/en/model_doc/owlv2)
- [Florence-2 open vocab detection on fal.ai](https://fal.ai/models/fal-ai/florence-2-large/open-vocabulary-detection)
- [Roboflow Florence-2 inference](https://inference.roboflow.com/foundation/florence2/)

**Minecraft AI agents (all non-detector, for context)**:
- [MineCLIP GitHub](https://github.com/MineDojo/MineCLIP)
- [MineDojo homepage](https://minedojo.org/)
- [STEVE-1 paper / site](https://sites.google.com/view/steve-1)
- [JARVIS-1 CraftJarvis](https://craftjarvis-jarvis1.github.io/)
- [JARVIS-VLA 2025](https://craftjarvis.github.io/JarvisVLA/)
- [Optimus-2 CVPR 2025](https://openaccess.thecvf.com/content/CVPR2025/papers/Li_Optimus-2_Multimodal_Minecraft_Agent_with_Goal-Observation-Action_Conditioned_Policy_CVPR_2025_paper.pdf)
- [MineStudio arxiv 2412.18293](https://arxiv.org/html/2412.18293v1)

**Minecraft supervised detectors (for comparison)**:
- [Roboflow Universe minecraft-ogpjp dataset](https://universe.roboflow.com/yolo-minecraft/minecraft-ogpjp)
- [Roboflow minecraft-mob-detection](https://universe.roboflow.com/minecraft-object-detection/minecraft-mob-detection)
- [Roboflow minecraft-tree-detector](https://universe.roboflow.com/david-coles/minecraft-tree-detector)
- [KSU undergrad Minecraft YOLO PoC 2023](https://digitalcommons.kennesaw.edu/undergradsymposiumksu/fall2023/presentations/106/)

**RTX 5090 / Blackwell PyTorch**:
- [pytorch/pytorch#159207 — sm_120 tracking](https://github.com/pytorch/pytorch/issues/159207)
- [pytorch/pytorch#164342 — official sm_120 in stable](https://github.com/pytorch/pytorch/issues/164342)
- [NVIDIA Developer Forum RTX 5090 + PyTorch](https://forums.developer.nvidia.com/t/rtx-5090-not-working-with-pytorch-and-stable-diffusion-sm-120-unsupported/338015)
- [PyTorch forum — is there a build that supports sm_120](https://discuss.pytorch.org/t/is-there-a-pytorch-build-that-supports-nvidia-rtx-5090-compute-capability-12-0-sm-120/223536)
- [Medium — getting PyTorch to actually use RTX 5090 on WSL2 (Mar 2026)](https://medium.com/@getnetdemil/getting-pytorch-to-actually-use-your-rtx-5090-a-complete-wsl2-setup-guide-for-blackwell-sm-120-61f86f64abc4)
- [Medium — Running Grounded-SAM-2 / GroundingDINO on RTX 5090 (Linux)](https://medium.com/@limyoonaxi/running-grounded-sam-2-groundingdino-and-pointnet-on-rtx-5090-setup-notes-and-pitfalls-478ffc79e457)
- [huggingface/transformers#31533 — HF GroundingDINO slower than original](https://github.com/huggingface/transformers/issues/31533)

**Supervision / ByteTrack**:
- [roboflow/supervision GitHub](https://github.com/roboflow/supervision)
- [Supervision docs](https://supervision.roboflow.com/)
- [Issue #1164 ByteTrack memory leak](https://github.com/roboflow/supervision/issues/1164)
- [Issue #1408 ByteTrack no detections](https://github.com/roboflow/supervision/issues/1408)
- [Issue #1670 min matching threshold](https://github.com/roboflow/supervision/issues/1670)
