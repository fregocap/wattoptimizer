import numpy as np
import pytest

from wattoptimizer import Course


@pytest.fixture(scope="session")
def hilly_out_and_back() -> Course:
    """12 km out-and-back with a 1 km ~5% climb on the way out."""
    x = np.arange(0.0, 12_001.0, 20.0)
    out = np.minimum(x, 12_000.0 - x)
    z = 100.0 + np.clip(out - 2_000.0, 0.0, 1_000.0) * 0.05 + 8.0 * np.sin(out / 700.0)
    heading = np.where(x < 6_000.0, 90.0, 270.0)
    return Course.from_profile(x, z, heading, ds=100.0, smoothing=40.0)
