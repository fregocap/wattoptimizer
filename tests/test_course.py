import numpy as np
import pytest

from wattoptimizer import Course, load_course
from wattoptimizer.demo import write_demo_gpx


def test_demo_gpx_roundtrip(tmp_path):
    course = load_course(write_demo_gpx(tmp_path / "demo.gpx"), ds=100.0)
    assert course.total_distance == pytest.approx(40_000.0, rel=0.01)
    assert course.n == pytest.approx(400, abs=2)
    assert 100.0 < course.elevation_gain < 400.0
    heading = np.degrees(course.heading) % 360
    assert np.median(heading[: course.n // 3]) == pytest.approx(90.0, abs=1.0)
    assert np.median(heading[-course.n // 3 :]) == pytest.approx(270.0, abs=1.0)


def test_profile_grade():
    x = np.linspace(0.0, 2_000.0, 101)
    course = Course.from_profile(x, 0.04 * x, 0.0, ds=100.0)
    assert np.allclose(course.grade, 0.04, atol=1e-3)
    assert course.total_distance == pytest.approx(2_000.0 * np.hypot(1.0, 0.04), rel=1e-3)


def test_too_short_course_is_rejected():
    with pytest.raises(ValueError):
        Course.from_profile([0.0, 50.0], [0.0, 0.0], 0.0, ds=100.0)
