"""Bounded global tempo/offset search. Never locally warp timing errors into good scores."""

import numpy as np


def global_alignment(measurements, notes, semitones=0):
    times = np.asarray(measurements["times"])[::5]
    hz = np.asarray([x or np.nan for x in measurements["f0"]])[::5]
    valid = np.isfinite(hz)
    if np.count_nonzero(valid) < 20:
        return {"available": False, "reason": "INSUFFICIENT_VOICING"}
    detected = np.full(len(hz), np.nan)
    detected[valid] = 69 + 12 * np.log2(hz[valid] / 440)
    # Build reference once, then sample its piecewise constant MIDI at shifted times.
    starts = np.array([n.start_ms / 1000 for n in notes])
    ends = np.array([n.end_ms / 1000 for n in notes])
    pitches = np.array([n.midi + semitones for n in notes])
    best = None
    for tempo in np.linspace(0.8, 1.25, 24):
        for offset in np.arange(-3.0, 3.01, 0.1):
            reference_time = (times - offset) * tempo
            index = np.searchsorted(starts, reference_time, side="right") - 1
            inside = (index >= 0) & (reference_time < ends[np.maximum(index, 0)])
            matched = inside & valid
            coverage = np.count_nonzero(matched) / max(1, np.count_nonzero(valid))
            if np.count_nonzero(matched) < 20 or coverage < 0.6:
                continue
            error = float(
                np.mean(np.minimum(12, np.abs(detected[matched] - pitches[index[matched]])))
            )
            objective = error + 6 * (1 - coverage) + 0.03 * abs(offset)
            if best is None or objective < best[0]:
                best = (objective, tempo, offset, error, coverage)
    if best is None or best[3] > 2.0 or best[4] < 0.7:
        return {"available": False, "reason": "ALIGNMENT_LOW_CONFIDENCE"}
    return {
        "available": True,
        "tempo_ratio": round(float(best[1]), 5),
        "offset_ms": round(best[2] * 1000),
        "coverage": round(best[4], 4),
        "mean_semitone_error": round(best[3], 4),
        "algorithm": "global-grid-v1",
    }
