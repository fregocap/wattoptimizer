"""Bioenergetic state equations: W' balance and carbohydrate (glycogen) depletion.

Like :mod:`wattoptimizer.physics`, functions accept ``xp`` = ``numpy`` or ``casadi``.
"""

from __future__ import annotations

import numpy as np

from .athlete import Athlete

CHO_ENERGY = 16_700.0  # J of metabolic energy per gram of carbohydrate oxidised
CHO_FRACTION_MIN = 0.1
CHO_FRACTION_MAX = 1.0
_FATIGUE_EPS = 20_000.0  # J, width of the smooth onset of durability decay


def _smooth_pos(x, eps, xp=np):
    """Differentiable max(x, 0)."""
    return 0.5 * (x + xp.sqrt(x * x + eps * eps))


def w_prime_rate(excess, deficit, w_bal, w_prime):
    """dW'bal/dt [W] from the differential W' balance model (Skiba et al., 2015).

    ``w_prime`` is the current W' capacity, which durability may have reduced.

    With ``excess`` = max(P - CP, 0) and ``deficit`` = max(CP - P, 0), the reserve
    drains one joule per joule of work above CP and recovers in proportion to both
    the CP deficit and how depleted it is:

        dW'/dt = -excess + deficit * (W'0 - W') / W'0

    Taking excess/deficit as inputs keeps the model exact in the optimiser, where
    they are non-negative decision variables with P - CP = excess - deficit.
    """
    return -excess + deficit * (w_prime - w_bal) / w_prime


def durability_factor(work, onset_kj: float, decay_per_mj: float, xp=np):
    """Fraction of the fresh value left after ``work`` [J] of counted work (durability).

    Linear decline of ``decay_per_mj`` per 1000 kJ beyond an onset of ``onset_kj``:

        D(E) = 1 - decay * max(E - E_onset, 0) / 1000 kJ

    with the kink at the onset smoothed over about 20 kJ, shifted so that D(0) = 1 exactly.
    """
    onset = 1000.0 * onset_kj
    beyond = _smooth_pos(work - onset, _FATIGUE_EPS, xp) - _smooth_pos(-onset, _FATIGUE_EPS, np)
    return 1.0 - decay_per_mj * beyond / 1e6


def cp_factor(work, athlete: Athlete, xp=np):
    """CP after ``work`` [J] of counted work, as a fraction of fresh CP."""
    return durability_factor(work, athlete.durability_onset, athlete.durability_cp_decay, xp)


def w_prime_factor(work, athlete: Athlete, xp=np):
    """W' capacity after ``work`` [J] of counted work, as a fraction of fresh W'."""
    return durability_factor(work, athlete.durability_onset, athlete.durability_w_prime_decay, xp)


def counted_work_rate(power, athlete: Athlete):
    """Rate [W] at which riding at ``power`` adds to the durability work counter."""
    return np.maximum(power - athlete.durability_power_threshold, 0.0)


def cho_fraction(power, athlete: Athlete, xp=np, cp_factor=1.0):
    """Fraction of metabolic energy supplied by carbohydrate, a logistic 'crossover' curve in P/CP.

    Intensity is relative to the *current* CP (``athlete.cp * cp_factor``), so once
    durability lowers CP the same watts burn a larger share of carbohydrate.
    """
    intensity = power / (athlete.cp * cp_factor)
    span = CHO_FRACTION_MAX - CHO_FRACTION_MIN
    return CHO_FRACTION_MIN + span / (1.0 + xp.exp(-athlete.cho_slope * (intensity - athlete.cho_crossover)))


def cho_oxidation(power, athlete: Athlete, xp=np, cp_factor=1.0):
    """Carbohydrate oxidation rate [g/s] at mechanical power ``power`` (see :func:`cho_fraction`)."""
    metabolic = power / athlete.gross_efficiency
    return cho_fraction(power, athlete, xp, cp_factor) * metabolic / CHO_ENERGY
