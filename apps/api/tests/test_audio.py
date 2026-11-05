import io
import json

import numpy as np
import pytest
import soundfile as sf

from app.audio import AssessmentInput, assess_bytes, decode_audio, score_measurements
from app.planning import PlanEdit, PlanningInput, create_plan


def wav(frequency=220, seconds=4, amplitude=0.2, stereo=False):
    t = np.arange(int(16000 * seconds)) / 16000
    x = amplitude * np.sin(2 * np.pi * frequency * t)
    if stereo:
        x = np.column_stack([x, -x])
    output = io.BytesIO()
    sf.write(output, x, 16000, format="WAV", subtype="FLOAT")
    return output.getvalue()


def test_long_tone_and_stereo_antiphase_are_measured_without_fake_accuracy():
    for stereo in (False, True):
        result = assess_bytes(
            wav(stereo=stereo), AssessmentInput(profile="long_tone", goal_duration_ms=4000)
        )
        assert result["total"]["available"] and result["total"]["value"] > 95
        assert abs(result["detected_f0_range_hz"][0] - 220) < 2
        json.dumps(result, allow_nan=False)
    free = assess_bytes(wav(), AssessmentInput())
    assert free["total"]["available"] is False and "pitch" not in free["dimensions"]


def test_bad_audio_silence_clipping_and_short_task_do_not_get_total():
    with pytest.raises(ValueError):
        decode_audio(b"not audio")
    for data in (wav(amplitude=0), wav(amplitude=2)):
        result = assess_bytes(data, AssessmentInput(profile="long_tone", goal_duration_ms=4000))
        assert result["quality"] == "quality_failed" and not result["total"]["available"]
    assert not assess_bytes(
        wav(seconds=2), AssessmentInput(profile="long_tone", goal_duration_ms=4000)
    )["total"]["available"]


def measurements(frequency=440, stop=2):
    times = np.arange(0.01, 2, 0.02)
    return {
        "times": times.tolist(),
        "f0": [frequency if t < stop else None for t in times],
        "hop_seconds": 0.02,
    }


def test_reference_rules_wrong_octave_and_missing_notes_are_not_hidden():
    config = AssessmentInput(
        profile="reference_song", reference_notes=[{"start_ms": 0, "end_ms": 2000, "midi": 69}]
    )
    correct = score_measurements(measurements(), config, 2, 0)
    assert correct["total"]["available"] and correct["total"]["value"] > 95
    octave = score_measurements(measurements(880), config, 2, 0)
    assert octave["dimensions"]["pitch"]["value"] == 0
    assert octave["dimensions"]["octave_error_ratio"]["value"] == 1
    assert not octave["total"]["available"]
    missing = score_measurements(measurements(stop=1), config, 2, 0)
    assert missing["reference_coverage"] < 0.8 and not missing["total"]["available"]
    assert abs(correct["dimensions"]["mean_signed_cents"]["value"]) < 0.01


def test_reference_input_and_plan_constraints():
    with pytest.raises(ValueError):
        AssessmentInput(profile="reference_song")
    with pytest.raises(ValueError):
        AssessmentInput(profile="long_tone")
    result = create_plan(PlanningInput(minutes=10, goal="pitch"))
    assert sum(item["seconds"] for item in result["plan"]["exercises"]) == 600
    assert result["plan"]["basis"] == "report_and_confirmed_feedback_v2"
    assert create_plan(PlanningInput(discomfort=True))["status"] == "refused"
    with pytest.raises(ValueError):
        PlanEdit(
            version=1,
            days=3,
            goal="pitch",
            exercises=[{"title": "x", "seconds": 30, "instruction": "x"}],
        )


@pytest.mark.parametrize("minutes", [5, 10, 30])
def test_generated_plans_can_be_edited_without_schema_conflicts(minutes):
    plan = create_plan(PlanningInput(minutes=minutes, goal="pitch"))["plan"]
    edited = PlanEdit(version=1, goal=plan["goal"], days=plan["days"], exercises=plan["exercises"])
    assert sum(item.seconds for item in edited.exercises) == minutes * 60
