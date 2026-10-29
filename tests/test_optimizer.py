import numpy as np
import pytest

from wattoptimizer import Athlete, ConstantWind, Course, InfeasibleError, RaceSpec, best_even_power, optimize, simulate
from wattoptimizer.optimizer import _round_positions


def _even(plan):
    return best_even_power(
        plan.course, plan.athlete, plan.race,
        head_wind=plan.head_wind, cda=plan.cda, cp=plan.cp_segment, v_max=plan.v_max,
    )


@pytest.fixture(scope="module")
def windy_plan(hilly_out_and_back):
    return optimize(hilly_out_and_back, Athlete(), RaceSpec(w_prime_final=0.2), wind=ConstantWind(4.0, 90.0))


def test_plan_meets_reserves(windy_plan):
    a = windy_plan.athlete
    assert windy_plan.w_bal.min() >= -1e-3
    assert windy_plan.w_bal[-1] >= 0.2 * a.w_prime - 1.0
    assert (windy_plan.power <= a.p_max + 1e-6).all()
    assert (windy_plan.speed <= windy_plan.v_max + 1e-6).all()


def test_plan_is_reproduced_by_forward_simulation(windy_plan):
    p = windy_plan
    sim = simulate(p.course, p.athlete, p.race, p.power,
                   head_wind=p.head_wind, cda=p.cda, cp=p.cp_segment, v_max=p.v_max)
    assert sim.total_time == pytest.approx(p.total_time, abs=0.5)
    assert np.abs(sim.w_bal - p.w_bal).max() < 50.0


def test_beats_even_pacing(windy_plan):
    power, sim = _even(windy_plan)
    assert windy_plan.total_time < sim.total_time


def test_pushes_harder_into_headwind_and_uphill(windy_plan):
    p = windy_plan
    out = p.head_wind > 0
    t = p.seg_time
    mean = lambda mask: (p.power[mask] * t[mask]).sum() / t[mask].sum()
    assert mean(out) > mean(~out)
    assert mean(p.course.grade > 0.03) > mean(np.abs(p.course.grade) < 0.01)


def test_glycogen_target_lowers_intensity(hilly_out_and_back):
    athlete = Athlete(glycogen=150.0, cho_intake=0.0)
    free = optimize(hilly_out_and_back, athlete, RaceSpec())
    target = free.glycogen[-1] + 30.0
    saving = optimize(hilly_out_and_back, athlete, RaceSpec(glycogen_final=target))
    assert saving.glycogen[-1] >= target - 0.1
    assert saving.avg_power < free.avg_power


def test_unreachable_target_raises(hilly_out_and_back):
    athlete = Athlete(glycogen=100.0, cho_intake=0.0)
    with pytest.raises(InfeasibleError):
        optimize(hilly_out_and_back, athlete, RaceSpec(glycogen_final=99.9))


def _steep_course():
    """5 km flat, then a 2 km 8% climb, then 3 km flat."""
    x = np.arange(0.0, 10_001.0, 20.0)
    z = np.clip(x - 5_000.0, 0.0, 2_000.0) * 0.08
    return Course.from_profile(x, z, 0.0)


def test_optimised_positions_sit_up_on_the_climb():
    course = _steep_course()
    athlete = Athlete(aero_cp_factor=0.9)
    plan = optimize(course, athlete, RaceSpec(w_prime_final=0.2))
    climb = course.grade > 0.06
    flat = np.abs(course.grade) < 0.01
    assert plan.climbing[climb].mean() > 0.9
    assert plan.climbing[flat].mean() < 0.1
    assert plan.relaxed_time is not None and plan.total_time - plan.relaxed_time < 2.0

    rules = optimize(course, athlete, RaceSpec(w_prime_final=0.2, position_mode="rules", climb_grade=0.12))
    assert plan.total_time < rules.total_time  # rules here never sit up


def test_no_cp_benefit_means_always_aero():
    plan = optimize(_steep_course(), Athlete(aero_cp_factor=1.0), RaceSpec())
    assert not plan.climbing.any()


def test_round_positions_merges_short_stints():
    course = Course.from_profile(np.linspace(0.0, 2_000.0, 101), np.zeros(101), 0.0, ds=100.0)
    a = np.zeros(course.n)
    a[5] = 1.0  # a single 100 m upright blip
    a[12:18] = 0.9  # a 600 m upright stint
    upright = _round_positions(course, a, min_run=300.0)
    assert not upright[:12].any()  # the blip is absorbed
    assert upright[12:].all()  # the stint is kept, and the 200 m aero tail merges into it
