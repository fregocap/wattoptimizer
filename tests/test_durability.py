from dataclasses import replace

import numpy as np
import pytest

from wattoptimizer import Athlete, Course, RaceSpec, optimize, physiology, simulate


@pytest.fixture(scope="module")
def flat():
    x = np.linspace(0.0, 20_000.0, 201)
    return Course.from_profile(x, np.zeros_like(x), 0.0)


@pytest.fixture(scope="module")
def rolling():
    x = np.arange(0.0, 30_001.0, 20.0)
    return Course.from_profile(x, 100.0 + 25.0 * np.sin(x / 1_500.0), 0.0)


FRAGILE = dict(durability_onset=0.0, durability_cp_decay=0.5, durability_w_prime_decay=0.5)


def test_factor_is_one_before_the_onset_and_linear_after():
    a = Athlete(durability_onset=1000.0, durability_cp_decay=0.1)
    assert physiology.cp_factor(0.0, a) == pytest.approx(1.0, abs=1e-4)
    assert physiology.cp_factor(3e6, a) == pytest.approx(1.0 - 0.1 * 2.0, abs=1e-4)  # 2000 kJ past onset
    assert physiology.w_prime_factor(3e6, a) == 1.0  # its decay is 0


def test_simulated_cp_declines_with_counted_work(flat):
    a = Athlete(**FRAGILE)
    sim = simulate(flat, a, RaceSpec(), 200.0)
    assert sim.work_counted[-1] == pytest.approx(200.0 * sim.total_time, rel=1e-9)
    assert np.all(np.diff(sim.cp_effective) < 0)
    assert sim.cp_effective[-1] == pytest.approx(a.cp * physiology.cp_factor(sim.work_counted[-1], a), rel=1e-3)


def test_power_threshold_only_counts_work_above_it(flat):
    a = Athlete(**FRAGILE, durability_power_threshold=150.0)
    sim = simulate(flat, a, RaceSpec(), 200.0)
    assert sim.work_counted[-1] == pytest.approx(50.0 * sim.total_time, rel=1e-9)


def test_w_prime_capacity_loss_comes_out_of_the_balance(flat):
    """Riding below CP keeps a fresh rider 'full', but the full tank shrinks."""
    a = Athlete(**FRAGILE)
    sim = simulate(flat, a, RaceSpec(), 150.0)
    capacity = a.w_prime * physiology.w_prime_factor(sim.work_counted, a)
    assert sim.w_bal == pytest.approx(capacity, rel=1e-5)  # midpoint rule lags by a few mJ


def test_durability_slows_the_plan_and_resimulates(rolling):
    race = RaceSpec(w_prime_final=0.2)
    fresh = optimize(rolling, Athlete(), race)
    tired = optimize(rolling, Athlete(**FRAGILE), race)
    assert tired.total_time > fresh.total_time
    assert tired.cp_effective[-1] < tired.cp_effective[0]

    sim = simulate(rolling, tired.athlete, race, tired.power, head_wind=tired.head_wind,
                   cda=tired.cda, cp=tired.cp_segment, v_max=tired.v_max)
    assert sim.total_time == pytest.approx(tired.total_time, abs=0.5)
    assert np.abs(sim.w_bal - tired.w_bal).max() < 100.0
    assert np.abs(sim.work_counted - tired.work_counted).max() < 1e3


def test_disabled_durability_changes_nothing(rolling):
    race = RaceSpec(w_prime_final=0.2)
    a = Athlete()
    assert not a.durability_enabled
    base = optimize(rolling, a, race)
    onset_only = optimize(rolling, replace(a, durability_onset=500.0), race)  # decays still 0
    assert onset_only.total_time == pytest.approx(base.total_time, abs=1e-6)
