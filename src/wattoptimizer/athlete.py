"""Athlete, equipment and race configuration."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class Athlete:
    """Rider, machine and physiology parameters."""

    rider_mass: float = 72.0  # kg
    bike_mass: float = 9.0  # kg, including bottles and kit
    cp: float = 280.0  # critical power [W]
    w_prime: float = 20_000.0  # work capacity above CP [J]
    p_max: float = 900.0  # cap on segment-average power [W]
    cda_aero: float = 0.23  # drag area on the aero bars [m^2]
    cda_climb: float = 0.30  # drag area seated upright [m^2]
    aero_cp_factor: float = 1.0  # CP in the aero position as a fraction of upright CP
    crr: float = 0.004  # rolling resistance coefficient
    drivetrain_efficiency: float = 0.975
    gross_efficiency: float = 0.21  # mechanical power / metabolic power
    glycogen: float = 500.0  # carbohydrate store at the start, muscle + liver [g]
    cho_intake: float = 80.0  # carbohydrate absorbed while riding [g/h]
    cho_crossover: float = 0.6  # intensity (P/CP) at the midpoint of the CHO-fraction curve
    cho_slope: float = 10.0  # steepness of the CHO-fraction curve
    rotating_mass: float = 1.0  # extra effective mass from wheel inertia [kg]

    @property
    def mass(self) -> float:
        return self.rider_mass + self.bike_mass

    @property
    def effective_mass(self) -> float:
        return self.mass + self.rotating_mass


@dataclass
class RaceSpec:
    """Race conditions, boundary conditions and riding-position rules."""

    start_time: datetime | None = None  # needed for time-varying (forecast) wind
    start_speed: float = 5.0  # speed at the start line / T1 mount line [m/s]
    w_prime_final: float = 0.0  # W'bal required at the finish/T2, as a fraction of W'
    glycogen_final: float = 0.0  # carbohydrate store required at the finish/T2 [g]
    temperature: float = 20.0  # air temperature [C], used for air density
    climb_grade: float = 0.05  # sit up when the grade exceeds this ...
    aero_min_speed: float = 0.0  # ... or the speed drops below this [m/s]; 0 disables
    max_speed: float = 22.0  # never exceed this, e.g. for safety on descents [m/s]
    lateral_accel: float = 4.0  # cornering limit, v^2 / r [m/s^2]
    wind_height_factor: float = 0.6  # wind at rider height / 10 m forecast wind


def load_config(path: str | Path) -> tuple[Athlete, RaceSpec]:
    """Read ``[athlete]`` and ``[race]`` tables from a TOML file."""
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    unknown = set(data) - {"athlete", "race"}
    if unknown:
        raise ValueError(f"{path}: unknown table(s) {sorted(unknown)}; expected [athlete] and [race]")
    athlete = _build(Athlete, data.get("athlete", {}), path)
    race = _build(RaceSpec, data.get("race", {}), path)
    if isinstance(race.start_time, str):
        race.start_time = datetime.fromisoformat(race.start_time)
    if race.start_time is not None and race.start_time.tzinfo is None:
        race.start_time = race.start_time.replace(tzinfo=timezone.utc)
    return athlete, race


def _build(cls, values: dict, path):
    allowed = {f.name for f in fields(cls)}
    unknown = set(values) - allowed
    if unknown:
        raise ValueError(f"{path}: unknown {cls.__name__} key(s) {sorted(unknown)}")
    return cls(**values)
