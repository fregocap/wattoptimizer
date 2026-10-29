"""Bicycle equations of motion, written once for both NumPy and CasADi.

Functions take an ``xp`` argument (``numpy`` or ``casadi``) that supplies ``sqrt``/``exp``.
With distance as the independent variable, each segment of length L obeys the
work-energy balance

    P * eta * dt  -  (F_resist(v_m) + F_brake) * L  =  1/2 * m_eff * (v1^2 - v0^2),
    v_m = (v0 + v1) / 2,   dt = L / v_m,

which is the trapezoidal discretisation of  m_eff * v * dv/ds = P * eta / v - F.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from .athlete import Athlete, RaceSpec
from .course import Course

G = 9.80665
R_DRY_AIR = 287.05
_AIR_SPEED_EPS = 0.01  # m^2/s^2, smooths |v| at zero air speed


@dataclass(frozen=True)
class SegmentParams:
    length: np.ndarray
    sin_slope: np.ndarray
    cos_slope: np.ndarray
    rho: np.ndarray
    mass: float
    effective_mass: float
    crr: float
    efficiency: float

    def as_casadi(self) -> SegmentParams:
        import casadi as ca

        return replace(
            self,
            length=ca.DM(self.length),
            sin_slope=ca.DM(self.sin_slope),
            cos_slope=ca.DM(self.cos_slope),
            rho=ca.DM(self.rho),
        )


def air_density(elevation, temperature_c: float):
    """Air density [kg/m^3] from the standard-atmosphere pressure at ``elevation`` [m]."""
    pressure = 101_325.0 * (1.0 - 2.25577e-5 * np.asarray(elevation)) ** 5.25588
    return pressure / (R_DRY_AIR * (temperature_c + 273.15))


def segment_params(course: Course, athlete: Athlete, temperature_c: float) -> SegmentParams:
    mid_elevation = 0.5 * (course.elevation[:-1] + course.elevation[1:])
    return SegmentParams(
        length=course.seg_length,
        sin_slope=course.sin_slope,
        cos_slope=course.cos_slope,
        rho=air_density(mid_elevation, temperature_c),
        mass=athlete.mass,
        effective_mass=athlete.effective_mass,
        crr=athlete.crr,
        efficiency=athlete.drivetrain_efficiency,
    )


def resistive_force(v, head_wind, cda, sp: SegmentParams, xp=np):
    """Aerodynamic drag + rolling resistance + gravity along the road [N]."""
    air = v + head_wind
    drag = 0.5 * sp.rho * cda * air * xp.sqrt(air * air + _AIR_SPEED_EPS)
    return drag + sp.mass * G * (sp.crr * sp.cos_slope + sp.sin_slope)


def energy_residual(v0, v1, power, head_wind, cda, brake_force, sp: SegmentParams, xp=np):
    """Work-energy balance of each segment [J]; zero when (v0, v1, power) are consistent."""
    vm = 0.5 * (v0 + v1)
    dt = sp.length / vm
    force = resistive_force(vm, head_wind, cda, sp, xp) + brake_force
    return power * sp.efficiency * dt - force * sp.length - 0.5 * sp.effective_mass * (v1 * v1 - v0 * v0)


def speed_limits(course: Course, race: RaceSpec) -> np.ndarray:
    """Maximum speed at each node from ``max_speed`` and the cornering limit v^2/r <= a_lat.

    The turn radius is estimated from the heading change between neighbouring segments,
    so corners tighter than the grid spacing are only resolved approximately.
    """
    turn = np.abs(np.angle(np.exp(1j * np.diff(course.heading))))
    arc = 0.5 * (course.seg_length[:-1] + course.seg_length[1:])
    radius = arc / np.maximum(turn, 1e-9)
    vmax = np.full(course.n + 1, race.max_speed)
    vmax[1:-1] = np.minimum(race.max_speed, np.sqrt(race.lateral_accel * radius))
    return vmax
