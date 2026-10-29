"""Open-source optimal pacing for time trials and triathlon bike legs."""

from .athlete import Athlete, RaceSpec, load_config
from .course import Course, load_course
from .optimizer import InfeasibleError, Plan, SolverOptions, optimize
from .simulate import best_even_power, simulate
from .wind import ConstantWind, GriddedWind, fetch_open_meteo

__all__ = [
    "Athlete",
    "ConstantWind",
    "Course",
    "GriddedWind",
    "InfeasibleError",
    "Plan",
    "RaceSpec",
    "SolverOptions",
    "best_even_power",
    "fetch_open_meteo",
    "load_config",
    "load_course",
    "optimize",
    "simulate",
]

__version__ = "0.1.0"
