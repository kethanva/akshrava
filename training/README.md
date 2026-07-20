# Model training & export (build plan §5.2, §5.5)

This folder holds the **fine-tune configuration and recipe**, not weights or images. No model
artifact is committed: shipping an unvalidated safety-critical detector would be dishonest, and
the frames are never persisted (Part 10). Produce weights locally, gate them, then bake them into
a private serving image (see `../Important Architecture.md` → model activation and release gates).

## Licence gate (do this first — plan Part 1)

Ultralytics **code and weights** are AGPL-3.0 unless you buy an Enterprise licence. AGPL can
require publishing the whole derivative service's source. For an NGO the clean path is usually to
open-source the project. If that is impossible, evaluate Apache-2.0 alternatives (YOLOX, RT-DETR
variants) and check **each** codebase's and weight-file's licence individually. The detector
stays `noop` (fail-closed) until this is resolved.

## Data (§5.2.2–5.2.4)

1. **IDD (Indian Driving Dataset)** — remap its labels onto the 12 in `akshrava.yaml`. Gets
   vehicles/people/animals to a usable baseline.
2. **Your own footage** — walk pilot routes with a phone **chest-mounted at ~1.35 m, ~12° down**
   (the fixed mount spec — geometry and data both depend on it). 8–10 h across morning/noon/dusk,
   dry/wet. Extract at 1 FPS, dedupe to ~6k distinct, label ~4,000.
3. **Hard negatives (20–30%)** — shadows, painted patches, speed breakers, puddles, wet-road
   reflections: the things that *look like* potholes and teach the model not to cry wolf.

Place labelled data under `../../datasets/akshrava/{images,labels}/{train,val,test}` with
**route-disjoint** splits.

## Train (§5.2.5)

```bash
yolo detect train model=yolo11s.pt data=training/akshrava.yaml imgsz=640 epochs=80 batch=16 mosaic=1.0
```

~6–10 h on a rented RTX 3090 (≈₹150–250/run), or free on Kaggle (30 GPU-hrs/week).

## Success bar (§5.2.6)

Track **recall, not mAP** on a held-out set of *your* walking footage:
- ≥0.85 recall on vehicles/people at <8 m
- ≥0.6 recall on potholes (hard; the cane is the backstop)

A retrain that improves mAP but regresses pothole recall at <8 m is **rejected** (§9.1).

## Export the reflex model (§5.5) — only if/when the reflex layer is adopted

```bash
yolo export model=best_11n.pt format=tflite int8=True imgsz=320 data=training/akshrava.yaml
```

INT8 needs calibration images (`data=`); use ~500 walking frames. **Record the exported file's
SHA-256 in the release manifest and set `YOLO_WEIGHTS_SHA256` in deployment** — a floating dependency in a safety component is how "it worked
last month" happens.

> Note: the on-device always-on reflex is currently **deferred** (see `../Important Architecture.md`). This
> export recipe is retained for the separate collision-research gate, not the current sprint.

## Spoken-scope pin (§8)

The 12 classes are for the **model**. Phase 1 **voices only four outcomes**: person, two-wheeler,
car/auto-like vehicle, large central obstruction. Everything else is logged, never spoken, until
its own validated data exists. The backend hazard scorer enforces this; do not widen it by editing
a training label.
