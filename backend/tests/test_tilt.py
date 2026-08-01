import cv2
import numpy as np

from app.tilt import estimate_tilt


def canvas():
    return np.full((480, 640, 3), 255, dtype=np.uint8)


def add_scene_verticals(img):
    for x in (40, 80, 560, 600):
        cv2.line(img, (x, 40), (x, 440), (0, 0, 0), 4)


def test_diagonal_cable_is_not_used_as_pole_axis():
    img = canvas()
    add_scene_verticals(img)
    cv2.rectangle(img, (300, 80), (320, 420), (30, 30, 30), -1)
    cv2.line(img, (200, 10), (430, 430), (0, 0, 0), 5)
    result = estimate_tilt(img, (285, 60, 335, 430))
    assert result["angle_is_valid"]
    assert result["tilt_degrees"] < 3


def test_wide_box_is_rejected():
    img = canvas()
    result = estimate_tilt(img, (100, 20, 500, 450))
    assert not result["angle_is_valid"]
    assert "bounding_box_too_wide" in result["rejection_reasons"]


def test_base_is_not_claimed_visible_without_ground_validation():
    img = canvas()
    add_scene_verticals(img)
    cv2.rectangle(img, (300, 80), (320, 420), (30, 30, 30), -1)
    result = estimate_tilt(img, (285, 60, 335, 430))
    assert result["base_visible"] is False
