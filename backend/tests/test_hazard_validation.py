import numpy as np

from app.hazard_validation import validate_construction_detection


def frame():
    # Neutral textured road-like image.
    img = np.full((720, 1280, 3), 125, dtype=np.uint8)
    for x in range(0, 1280, 20):
        img[450:710, x:x+3] = 80
    return img


def test_rejects_box_high_in_scene():
    result = validate_construction_detection(frame(), [200, 40, 500, 250], 0.9, "construction_debris")
    assert not result.passed
    assert "object_not_on_ground_region" in result.reasons


def test_rejects_weak_detector_confidence():
    result = validate_construction_detection(frame(), [300, 470, 700, 700], 0.25, "construction_materials")
    assert not result.passed
    assert "detector_confidence_below_construction_floor" in result.reasons


def test_rejects_scene_sized_box():
    result = validate_construction_detection(frame(), [0, 200, 1280, 720], 0.9, "construction_debris")
    assert not result.passed
    assert "box_too_large_or_scene_level" in result.reasons


def test_ground_candidate_can_pass_scene_validation():
    result = validate_construction_detection(frame(), [300, 470, 850, 710], 0.9, "construction_debris")
    assert result.public_space_likelihood >= 0.55
    assert result.estimated_size in {"medium", "large"}
