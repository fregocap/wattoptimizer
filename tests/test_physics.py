import numpy as np
import pytest
from scipy.optimize import brentq

from wattoptimizer import Athlete, Course, RaceSpec, simulate
from wattoptimizer import physics
from wattoptimizer.wind import ConstantWind, head_wind_component


def test_head_wind_sign():
    u, v = ConstantWind(5.0, 90.0).sample(np.zeros(1), np.zeros(1), None)  # easterly wind
    assert head_wind_component(u, v, np.radians(90.0))[0] == pytest.approx(5.0)  # riding east: headwind
    assert head_wind_component(u, v, np.radians(270.0))[0] == pytest.approx(-5.0)  # riding west: tailwind
    assert head_wind_component(u, v, 0.0)[0] == pytest.approx(0.0, abs=1e-12)  # crosswind


def test_air_density_sea_level():
    assert physics.air_density(0.0, 15.0) == pytest.approx(1.225, rel=1e-3)
    assert physics.air_density(1500.0, 15.0) < physics.air_density(0.0, 15.0)


def test_flat_steady_speed_matches_power_balance():
    x = np.linspace(0.0, 20_000.0, 201)
    course = Course.from_profile(x, np.zeros_like(x), 0.0)
    athlete, race = Athlete(), RaceSpec(start_speed=10.0)
    sim = simulate(course, athlete, race, 250.0)

    rho = physics.air_density(0.0, race.temperature)

    def balance(v):
        drag = 0.5 * rho * athlete.cda_aero * v**2 + athlete.crr * athlete.mass * physics.G
        return 250.0 * athlete.drivetrain_efficiency - drag * v

    assert sim.speed[-1] == pytest.approx(brentq(balance, 1.0, 30.0), rel=1e-3)


def test_w_prime_drains_above_cp_and_recovers_below():
    x = np.linspace(0.0, 10_000.0, 101)
    course = Course.from_profile(x, np.zeros_like(x), 0.0)
    athlete = Athlete()
    power = np.where(np.arange(course.n) < 50, athlete.cp + 100.0, athlete.cp - 100.0)
    sim = simulate(course, athlete, RaceSpec(), power)
    half = course.n // 2
    t_above = sim.time[half]
    assert sim.w_bal[half] == pytest.approx(athlete.w_prime - 100.0 * t_above, rel=1e-6)
    assert sim.w_bal[-1] > sim.w_bal[half]


def test_cornering_limit_at_turnaround(hilly_out_and_back):
    vmax = physics.speed_limits(hilly_out_and_back, RaceSpec())
    turn = hilly_out_and_back.n // 2
    assert vmax[turn] < 12.0
    assert np.median(vmax) == RaceSpec().max_speed
