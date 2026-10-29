"""Bioenergetic state equations: W' balance and carbohydrate (glycogen) depletion.

Like :mod:`wattoptimizer.physics`, functions accept ``xp`` = ``numpy`` or ``casadi``.
"""

from __future__ import annotations

import numpy as np

from .athlete import Athlete

CHO_ENERGY = 16_700.0  # J of metabolic energy per gram of carbohydrate oxidised
CHO_FRACTION_MIN = 0.1
CHO_FRACTION_MAX = 1.0


def w_prime_rate(excess, deficit, w_bal, w_prime):
    """dW'bal/dt [W] from the differential W' balance model (Skiba et al., 2015).

    With ``excess`` = max(P - CP, 0) and ``deficit`` = max(CP - P, 0), the reserve
    drains one joule per joule of work above CP and recovers in proportion to both
    the CP deficit and how depleted it is:

        dW'/dt = -excess + deficit * (W'0 - W') / W'0

    Taking excess/deficit as inputs keeps the model exact in the optimiser, where
    they are non-negative decision variables with P - CP = excess - deficit.
    """
    return -excess + deficit * (w_prime - w_bal) / w_prime


def cho_fraction(power, athlete: Athlete, xp=np):
    """Fraction of metabolic energy supplied by carbohydrate, a logistic 'crossover' curve in P/CP."""
    intensity = power / athlete.cp
    span = CHO_FRACTION_MAX - CHO_FRACTION_MIN
    return CHO_FRACTION_MIN + span / (1.0 + xp.exp(-athlete.cho_slope * (intensity - athlete.cho_crossover)))


def cho_oxidation(power, athlete: Athlete, xp=np):
    """Carbohydrate oxidation rate [g/s] at mechanical power ``power``."""
    metabolic = power / athlete.gross_efficiency
    return cho_fraction(power, athlete, xp) * metabolic / CHO_ENERGY
