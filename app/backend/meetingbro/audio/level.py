"""Measure unconditioned input, independent of transcription and gain control."""
import math
import numpy as np


def audio_level(samples: np.ndarray) -> dict:
    if not samples.size:
        return {"level": 0.0, "db": -80.0}
    clean = np.nan_to_num(np.asarray(samples, dtype=np.float64), nan=0., posinf=1., neginf=-1.)
    rms = float(np.sqrt(np.mean(clean ** 2)))
    db = max(-80., min(0., 20 * math.log10(max(rms, 1e-8))))
    return {"level": max(0., min(1., (db + 60.) / 60.)), "db": db}
