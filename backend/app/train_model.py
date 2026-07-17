"""Export labeled training samples to a YOLO dataset and train a detector.

Run:  python -m app.train_model [epochs]

Pilot reality: with a handful of close-up asset photos this produces a rough,
directional model — not a production detector. Samples without an explicit
bounding box are assumed to be close-ups where the asset fills most of the
frame (a near-full-frame default box). When enough boxed samples per class
exist, drop the default and the same script yields a real model.

The trained weights are written under backend/models/asset_detector/. Point
settings.model_path at best.pt to have the worker use it.
"""
import shutil
import sys
from pathlib import Path

from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.entities import TrainingSample

DEFAULT_BOX = (0.5, 0.5, 0.9, 0.9)  # cx, cy, w, h — close-up fills the frame


def build_dataset(root: Path) -> tuple[Path, list[str], int]:
    if root.exists():
        shutil.rmtree(root)
    img_dir = root / "images" / "train"
    lbl_dir = root / "labels" / "train"
    img_dir.mkdir(parents=True, exist_ok=True)
    lbl_dir.mkdir(parents=True, exist_ok=True)

    with SessionLocal() as db:
        samples = db.scalars(select(TrainingSample).order_by(TrainingSample.id)).all()
        classes = sorted({s.asset_type for s in samples})
        class_id = {c: i for i, c in enumerate(classes)}
        n = 0
        for s in samples:
            src = Path(s.filename)
            if not src.is_file():
                continue
            stem = f"s{s.id}"
            shutil.copy(src, img_dir / f"{stem}{src.suffix or '.jpg'}")
            cx = s.bbox_cx if s.bbox_cx is not None else DEFAULT_BOX[0]
            cy = s.bbox_cy if s.bbox_cy is not None else DEFAULT_BOX[1]
            w = s.bbox_w if s.bbox_w is not None else DEFAULT_BOX[2]
            h = s.bbox_h if s.bbox_h is not None else DEFAULT_BOX[3]
            (lbl_dir / f"{stem}.txt").write_text(f"{class_id[s.asset_type]} {cx} {cy} {w} {h}\n")
            n += 1

    # tiny dataset: validate on the same images (overfit metrics, but directional)
    names = "\n".join(f"  {i}: {c}" for i, c in enumerate(classes))
    (root / "data.yaml").write_text(
        f"path: {root}\ntrain: images/train\nval: images/train\nnames:\n{names}\n"
    )
    return root / "data.yaml", classes, n


def main():
    epochs = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    root = Path(settings.upload_dir).resolve().parent / "training_run"
    data_yaml, classes, n = build_dataset(root)
    print(f"dataset: {n} images, {len(classes)} classes: {classes}")
    if n == 0:
        print("no usable samples; aborting")
        return

    from ultralytics import YOLO
    out_dir = Path(__file__).resolve().parent.parent / "models"
    model = YOLO("yolo11n.pt")
    model.train(
        data=str(data_yaml), epochs=epochs, imgsz=480, batch=4, workers=0,
        device="cpu", project=str(out_dir), name="asset_detector", exist_ok=True,
        patience=0, verbose=True,
    )
    best = out_dir / "asset_detector" / "weights" / "best.pt"
    print(f"\nDONE. weights: {best}")
    print("To activate: set MODEL_PATH to that path in backend/.env and restart streetscan-worker.")


if __name__ == "__main__":
    main()
