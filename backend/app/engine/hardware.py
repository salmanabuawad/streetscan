"""Hardware detection — never fail when there's no GPU; adapt instead."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class HardwareProfile:
    device: str            # cuda | cpu
    gpu_name: str | None
    batch_size: int
    imgsz: int
    workers: int
    warning: str | None


def detect_hardware() -> HardwareProfile:
    try:
        import torch
        if torch.cuda.is_available():
            return HardwareProfile("cuda", torch.cuda.get_device_name(0), 8, 640,
                                   int(os.getenv("ANALYSIS_WORKERS", "4")), None)
    except Exception:
        pass
    # CPU fallback: smaller batch/resolution, single worker, slow-processing note
    return HardwareProfile("cpu", None, 1, 480, 1,
                           "no GPU detected — running on CPU with reduced batch/resolution (slower)")
