"""Transparent monophonic engineering measurements, not a trained vocal diagnosis model."""

from __future__ import annotations

import io
import math
from typing import Literal
from uuid import UUID

import numpy as np
import soundfile as sf
from pydantic import BaseModel, ConfigDict, Field, model_validator
from scipy.signal import correlate, find_peaks, resample_poly

VERSION = "mono-autocorrelation-v1"
RULESET = "engineering-v1"


class ReferenceNote(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    start_ms: int = Field(ge=0, le=600000)
    end_ms: int = Field(gt=0, le=600000)
    midi: int = Field(ge=24, le=96)

    @model_validator(mode="after")
    def valid_interval(self):
        if self.end_ms <= self.start_ms:
            raise ValueError("note end must follow start")
        return self


class AssessmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    profile: Literal["free_singing", "long_tone", "reference_song"] = "free_singing"
    goal_duration_ms: int | None = Field(default=None, ge=3000, le=120000)
    reference_notes: list[ReferenceNote] = Field(default_factory=list, max_length=3000)
    semitones: int = Field(default=0, ge=-12, le=12)
    offset_ms: int = Field(default=0, ge=-10000, le=10000)
    tempo_ratio: float = Field(default=1.0, ge=0.8, le=1.25)
    text: str = Field(default="", max_length=4000)
    input_asset_id: str | None = None
    song_id: UUID | None = None
    auto_align: bool = False
    channel_mode: Literal["loudest", "left", "right", "center"] = "loudest"
    separate_vocals: bool = False

    @model_validator(mode="after")
    def profile_requirements(self):
        if self.profile == "long_tone" and self.goal_duration_ms is None:
            raise ValueError("long_tone requires a target duration before analysis")
        if self.profile == "reference_song" and not self.reference_notes and not self.song_id:
            raise ValueError("reference_song requires reference notes")
        for previous, current in zip(self.reference_notes, self.reference_notes[1:]):
            if current.start_ms < previous.end_ms:
                raise ValueError("reference notes must be sorted and non-overlapping")
        return self


def decode_audio(data: bytes, channel_mode: str = "loudest") -> tuple[np.ndarray, int]:
    if not data or len(data) > 104857600:
        raise ValueError("AUDIO_SIZE_INVALID")
    try:
        info = sf.info(io.BytesIO(data))
        if (
            info.duration > 600
            or info.duration < 0.1
            or info.channels not in (1, 2)
            or not 8000 <= info.samplerate <= 192000
        ):
            raise ValueError("AUDIO_FORMAT_LIMIT")
        # Reject excessive decoded allocations before loading compressed audio.
        if info.frames * info.channels > 600 * 48000 * 2:
            raise ValueError("AUDIO_DECODE_LIMIT")
        samples, rate = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
    except (sf.LibsndfileError, RuntimeError) as exc:
        raise ValueError("AUDIO_DECODE_FAILED") from exc
    if not np.isfinite(samples).all():
        raise ValueError("NONFINITE_AUDIO")
    # Opposite-phase stereo is not silently averaged into a fake silence result.
    if channel_mode == "center":
        if samples.shape[1] != 2:
            raise ValueError("AUDIO_STEREO_REQUIRED")
        # Mid-channel extraction preserves centered voice, but is not neural stem separation.
        mono = np.mean(samples, axis=1)
    elif channel_mode in {"left", "right"}:
        index = 0 if channel_mode == "left" else 1
        if samples.shape[1] <= index:
            raise ValueError("AUDIO_CHANNEL_UNAVAILABLE")
        mono = samples[:, index]
    else:
        mono = samples[:, np.argmax(np.mean(samples.astype(np.float64) ** 2, axis=0))]
    if rate != 16000:
        divisor = math.gcd(rate, 16000)
        mono = resample_poly(mono, 16000 // divisor, rate // divisor)
    return mono, 16000


def track(samples: np.ndarray, rate: int) -> dict:
    window, hop = 1024, 320
    f0, confidence, rms, timestamps = [], [], [], []
    min_lag, max_lag = int(rate / 1100), int(rate / 65)
    for start in range(0, max(1, len(samples) - window + 1), hop):
        frame = samples[start : start + window].astype(float)
        energy = float(np.sqrt(np.mean(frame**2))) if frame.size else 0.0
        frequency, strength = None, 0.0
        if len(frame) == window and energy > 0.008:
            frame -= np.mean(frame)
            ac = correlate(frame, frame, mode="full", method="fft")[window - 1 :]
            ac = ac / (np.arange(window, 0, -1) * (np.mean(frame**2) + 1e-12))
            peaks, _ = find_peaks(ac[min_lag : max_lag + 1])
            peaks = peaks + min_lag
            if peaks.size:
                best = float(np.max(ac[peaks]))
                eligible = peaks[ac[peaks] >= max(0.70, 0.9 * best)]
                if eligible.size:
                    lag = int(eligible[0])
                    denominator = ac[lag - 1] - 2 * ac[lag] + ac[lag + 1]
                    shift = (
                        0.5 * (ac[lag - 1] - ac[lag + 1]) / denominator
                        if abs(denominator) > 1e-12
                        else 0.0
                    )
                    frequency, strength = (
                        float(rate / (lag + np.clip(shift, -0.5, 0.5))),
                        float(min(1.0, ac[lag])),
                    )
        f0.append(frequency)
        confidence.append(strength)
        rms.append(energy)
        timestamps.append((start + window / 2) / rate)
    return {
        "times": timestamps,
        "f0": f0,
        "confidence": confidence,
        "rms": rms,
        "hop_seconds": hop / rate,
    }


def unavailable(reason: str) -> dict:
    return {"available": False, "value": None, "reason": reason}


def metric(value: float, unit: str = "score") -> dict:
    return {
        "available": True,
        "value": round(float(value), 4),
        "unit": unit,
        "algorithm_version": VERSION,
    }


def longest_duration(mask: np.ndarray, hop: float) -> float:
    longest = current = 0
    for valid in mask:
        current = current + 1 if valid else 0
        longest = max(longest, current)
    return longest * hop


def score_measurements(
    measurements: dict, config: AssessmentInput, duration: float, clipping: float
) -> dict:
    times = np.array(measurements["times"])
    f0 = np.array([value if value else np.nan for value in measurements["f0"]])
    valid = np.isfinite(f0)
    hop = measurements["hop_seconds"]
    coverage = min(1.0, float(np.count_nonzero(valid) * hop / duration))
    quality = (
        "quality_failed"
        if not valid.any() or clipping > 0.05
        else "limited"
        if coverage < 0.8
        else "usable"
    )
    report = {
        "status": "measured",
        "profile": config.profile,
        "algorithm_version": VERSION,
        "ruleset_version": RULESET,
        "quality": quality,
        "duration_ms": round(duration * 1000),
        "voiced_seconds": round(float(np.count_nonzero(valid) * hop), 3),
        "tracking_coverage": round(coverage, 4),
        "clipping_ratio": round(clipping, 5),
        "total": unavailable("REFERENCE_OR_DEFINED_TASK_REQUIRED"),
        "dimensions": {},
        "segments": [],
        "limitations": [
            "仅限单人清唱；伴奏、噪声、混响与自然颤音会影响测量。",
            "工程练习参考指标，尚无教师标注校准；不判断疾病、音色优劣或发声原因。",
        ],
    }
    if quality == "quality_failed":
        report["total"] = unavailable("AUDIO_QUALITY_FAILED")
        return report
    if valid.any():
        report["detected_f0_range_hz"] = [
            round(float(np.min(f0[valid])), 2),
            round(float(np.max(f0[valid])), 2),
        ]
    if config.profile == "long_tone":
        goal = config.goal_duration_ms / 1000
        selection = (times <= goal) & valid
        task_coverage = min(1.0, float(np.count_nonzero(selection) * hop / goal))
        report["task_coverage"] = round(task_coverage, 4)
        if task_coverage < 0.8 or duration < goal or np.count_nonzero(selection) * hop < 3:
            report["total"] = unavailable("INSUFFICIENT_LONG_TONE_COVERAGE")
            return report
        cents = 1200 * np.log2(f0[selection] / np.median(f0[selection]))
        mad = float(np.median(np.abs(cents)))
        stability = 100 * max(0.0, 1 - mad / 100)
        sustain = 100 * min(1.0, longest_duration(selection, hop) / goal)
        report["dimensions"] = {
            "stability": metric(stability),
            "sustain": metric(sustain),
            "mad_cents": metric(mad, "cents"),
        }
        report["total"] = metric(0.7 * stability + 0.3 * sustain)
    elif config.profile == "reference_song":
        return reference_scores(report, times, f0, valid, hop, config)
    return report


def reference_scores(report, times, f0, valid, hop, config):
    signed_errors, absolute_errors = [], []
    total_duration = matched_duration = 0.0
    onsets = []
    previous_midi = None
    last_onset = -1.0
    for time, frequency in zip(times, f0, strict=True):
        if not np.isfinite(frequency):
            previous_midi = None
            continue
        midi = round(69 + 12 * math.log2(frequency / 440))
        if (previous_midi is None or abs(midi - previous_midi) >= 1) and time - last_onset >= 0.1:
            onsets.append((float(max(0.0, time - 0.032)), midi))
            last_onset = time
        previous_midi = midi
    rhythm_errors, rhythm_weights, consumed = [], [], set()
    for note in config.reference_notes:
        start = (note.start_ms / config.tempo_ratio + config.offset_ms) / 1000
        end = (note.end_ms / config.tempo_ratio + config.offset_ms) / 1000
        expected_duration = end - start
        selection = (times >= start) & (times < end) & valid
        covered = min(expected_duration, np.count_nonzero(selection) * hop)
        target = 440 * 2 ** ((note.midi + config.semitones - 69) / 12)
        cents = 1200 * np.log2(f0[selection] / target)
        total_duration += expected_duration
        matched_duration += covered
        absolute_errors.extend(np.abs(cents).tolist())
        signed_errors.extend(cents.tolist())
        candidates = [
            (i, abs(time - start))
            for i, (time, midi) in enumerate(onsets)
            if i not in consumed
            and abs(midi - (note.midi + config.semitones)) <= 1
            and abs(time - start) <= 0.3
        ]
        if candidates:
            chosen, residual = min(candidates, key=lambda pair: pair[1])
            consumed.add(chosen)
            rhythm_errors.append(residual)
            rhythm_weights.append(expected_duration)
        report["segments"].append(
            {
                "start_ms": note.start_ms,
                "end_ms": note.end_ms,
                "coverage": round(covered / expected_duration, 4),
                "mean_abs_cents": round(float(np.mean(np.abs(cents))), 3) if cents.size else None,
            }
        )
    coverage = min(1.0, matched_duration / total_duration)
    pitch = 100 * max(0.0, 1 - float(np.mean(absolute_errors)) / 200) if absolute_errors else None
    rhythm_coverage = sum(rhythm_weights) / total_duration
    rhythm = (
        100 * max(0.0, 1 - float(np.average(rhythm_errors, weights=rhythm_weights)) / 0.3)
        if rhythm_errors
        else None
    )
    in_reference = np.zeros(len(times), dtype=bool)
    for note in config.reference_notes:
        in_reference |= (
            times >= (note.start_ms / config.tempo_ratio + config.offset_ms) / 1000
        ) & (times < (note.end_ms / config.tempo_ratio + config.offset_ms) / 1000)
    outside = valid & ~in_reference
    extra_ratio = float(np.count_nonzero(outside) * hop / max(0.001, total_duration))
    report.update(
        {
            "reference_coverage": round(coverage, 4),
            "onset_match_coverage": round(rhythm_coverage, 4),
            "offset_ms": config.offset_ms,
            "tempo_ratio": config.tempo_ratio,
            "semitones": config.semitones,
            "alignment_version": "fixed-reference-nearest-onset-v1",
            "false_positive_voicing_ratio": round(extra_ratio, 4),
        }
    )
    report["dimensions"] = {
        "pitch": metric(pitch) if pitch is not None else unavailable("NO_F0"),
        "rhythm": metric(rhythm)
        if rhythm is not None and rhythm_coverage >= 0.8
        else unavailable("ONSET_ALIGNMENT_UNRELIABLE"),
        "coverage": metric(100 * coverage),
        "mean_signed_cents": metric(float(np.mean(signed_errors)), "cents")
        if signed_errors
        else unavailable("NO_F0"),
        "octave_error_ratio": metric(float(np.mean(np.abs(signed_errors) > 1000)), "ratio")
        if signed_errors
        else unavailable("NO_F0"),
    }
    if (
        coverage >= 0.8
        and rhythm_coverage >= 0.8
        and extra_ratio <= 0.2
        and pitch is not None
        and rhythm is not None
    ):
        report["total"] = metric((0.6 * pitch + 0.4 * rhythm) * coverage)
    else:
        report["total"] = unavailable("REFERENCE_ALIGNMENT_OR_COVERAGE_INSUFFICIENT")
    return report


def assess_bytes(data: bytes, config: AssessmentInput) -> dict:
    samples, rate = decode_audio(data, config.channel_mode)
    measurements = track(samples, rate)
    clipping = float(np.mean(np.abs(samples) >= 0.99))
    alignment = None
    if config.auto_align and config.reference_notes:
        from app.alignment import global_alignment

        alignment = global_alignment(measurements, config.reference_notes, config.semitones)
        if alignment["available"]:
            config = config.model_copy(
                update={
                    "offset_ms": alignment["offset_ms"],
                    "tempo_ratio": alignment["tempo_ratio"],
                }
            )
    report = score_measurements(measurements, config, len(samples) / rate, clipping)
    report["alignment"] = alignment
    report["channel_mode"] = config.channel_mode
    if config.reference_notes:
        import hashlib
        import json

        report["reference_fingerprint"] = hashlib.sha256(
            json.dumps(
                {
                    "notes": [n.model_dump() for n in config.reference_notes],
                    "semitones": config.semitones,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        report["reference_contour"] = [
            {
                "start": (n.start_ms / config.tempo_ratio + config.offset_ms) / 1000,
                "end": (n.end_ms / config.tempo_ratio + config.offset_ms) / 1000,
                "hz": 440 * 2 ** ((n.midi + config.semitones - 69) / 12),
            }
            for n in config.reference_notes
        ]
    step = max(1, len(measurements["times"]) // 1500)
    report["pitch_contour"] = [
        {"time": t, "hz": f}
        for t, f in zip(measurements["times"][::step], measurements["f0"][::step], strict=True)
    ]
    if alignment and not alignment["available"]:
        report["total"] = unavailable("AUTOMATIC_ALIGNMENT_UNRELIABLE")
    return report
