"""Command-line interface: ``wattopt plan`` and ``wattopt demo-course``."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import report
from .athlete import Athlete, RaceSpec, load_config
from .course import load_course
from .optimizer import InfeasibleError, SolverOptions, optimize
from .simulate import best_even_power
from .wind import ConstantWind, fetch_open_meteo


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wattopt", description="Optimal pacing for TTs and triathlon bike legs.")
    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="optimise a power plan for a course")
    plan.add_argument("course", type=Path, help=".gpx or .fit file")
    plan.add_argument("-c", "--config", type=Path, help="TOML file with [athlete] and [race] tables")
    plan.add_argument("--ds", type=float, default=100.0, help="grid spacing [m] (default: 100)")
    plan.add_argument("--elevation-smoothing", type=float, default=100.0, help="elevation filter width [m]")
    plan.add_argument("--cp", type=float, help="override critical power [W]")
    plan.add_argument("--w-prime", type=float, help="override W' [J]")
    plan.add_argument("--rider-mass", type=float, help="override rider mass [kg]")
    plan.add_argument("--start", help="start time, ISO 8601 with UTC offset, e.g. 2026-07-12T07:30+02:00")
    wind = plan.add_mutually_exclusive_group()
    wind.add_argument("--weather", action="store_true", help="fetch hourly wind along the course from Open-Meteo")
    wind.add_argument("--wind-speed", type=float, help="constant 10 m wind speed [m/s]")
    plan.add_argument("--wind-from", type=float, default=0.0, help="direction the wind blows from [deg]")
    plan.add_argument("--w-prime-final", type=float, help="W'bal fraction required at the finish (0-1)")
    plan.add_argument("--glycogen-final", type=float, help="glycogen required at the finish [g]")
    plan.add_argument("--positions", choices=["optimize", "rules"], help="choose aero/upright by optimisation or rules")
    plan.add_argument("--split", type=float, default=1000.0, help="split length [m] (default: 1000)")
    plan.add_argument("--csv", type=Path, help="write the splits to a CSV file")
    plan.add_argument("--plot", type=Path, help="save a chart of the plan (needs matplotlib)")
    plan.add_argument("--no-baseline", action="store_true", help="skip the even-pacing comparison")
    plan.add_argument("-v", "--verbose", action="store_true", help="show IPOPT output")

    demo = sub.add_parser("demo-course", help="write a synthetic 40 km out-and-back TT as GPX")
    demo.add_argument("output", type=Path)

    args = parser.parse_args(argv)
    if args.command == "demo-course":
        from .demo import write_demo_gpx

        print(f"wrote {write_demo_gpx(args.output)}")
        return 0
    try:
        return _plan(args)
    except (InfeasibleError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _plan(args) -> int:
    athlete, race = load_config(args.config) if args.config else (Athlete(), RaceSpec())
    for attr, value in (("cp", args.cp), ("w_prime", args.w_prime), ("rider_mass", args.rider_mass)):
        if value is not None:
            setattr(athlete, attr, value)
    if args.w_prime_final is not None:
        race.w_prime_final = args.w_prime_final
    if args.glycogen_final is not None:
        race.glycogen_final = args.glycogen_final
    if args.positions:
        race.position_mode = args.positions
    if args.start:
        race.start_time = datetime.fromisoformat(args.start)
        if race.start_time.tzinfo is None:
            race.start_time = race.start_time.replace(tzinfo=timezone.utc)

    course = load_course(args.course, ds=args.ds, smoothing=args.elevation_smoothing)
    field = None
    if args.wind_speed is not None:
        field = ConstantWind(args.wind_speed, args.wind_from)
    elif args.weather:
        if race.start_time is None:
            raise ValueError("--weather needs a start time (--start or race.start_time)")
        # generous window: slow riders and long courses
        duration = timedelta(seconds=course.total_distance / 5.0)
        print("fetching wind from Open-Meteo ...", file=sys.stderr)
        field = fetch_open_meteo(course, race.start_time, duration)
        t0 = race.start_time.timestamp()
        temperature = field.mean_temperature(t0, t0 + duration.total_seconds())
        if temperature is not None:
            race.temperature = temperature

    plan = optimize(course, athlete, race, wind=field, options=SolverOptions(verbose=args.verbose))
    baseline = None
    if not args.no_baseline:
        baseline = best_even_power(
            course, athlete, race, head_wind=plan.head_wind, cda=plan.cda, cp=plan.cp_segment, v_max=plan.v_max
        )

    rows = report.splits(plan, args.split)
    print(report.summary(plan, baseline))
    print()
    print(report.format_table(rows))
    if args.csv:
        report.write_csv(rows, args.csv)
        print(f"\nwrote {args.csv}")
    if args.plot:
        from .plot import plot_plan

        plot_plan(plan, args.plot)
        print(f"wrote {args.plot}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
