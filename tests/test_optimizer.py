import numpy as np
import pytest

from wattoptimizer import Athlete, ConstantWind, InfeasibleError, RaceSpec, best_even_power, optimize, simulate


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
