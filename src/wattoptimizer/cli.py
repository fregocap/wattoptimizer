"""Command-line interface: ``wattopt plan`` and ``wattopt demo-course``."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from urllib.error import HTTPError
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

    out = plan.add_argument_group("workout export")
    out.add_argument("--steps", type=int, default=20, help="number of distance-based workout steps (default: 20)")
    out.add_argument("--workout", type=Path, help="write the steps as intervals.icu workout text")
    out.add_argument("--export", choices=["intervals"], help="upload the steps as a planned workout")
    out.add_argument("--date", type=date.fromisoformat, help="workout date, YYYY-MM-DD (default: the start date)")
    out.add_argument("--intervals-athlete", default="0", help="intervals.icu athlete id (default: the key's owner)")
    out.add_argument("--dry-run", action="store_true", help="print the upload instead of sending it")

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
    except HTTPError as exc:
        print(f"error: intervals.icu answered {exc.code} {exc.reason}: {exc.read().decode(errors='replace')[:300]}",
              file=sys.stderr)
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
    if args.workout or args.export:
        return _export(args, plan, baseline)
    return 0


def _export(args, plan, baseline) -> int:
    from . import export

    day = args.date or (plan.race.start_time.date() if plan.race.start_time else None)
    if args.export and day is None:
        raise ValueError("--export needs --date or a start time")
    api_key = os.environ.get("INTERVALS_API_KEY")
    if args.export and not args.dry_run and not api_key:
        raise ValueError("set INTERVALS_API_KEY (intervals.icu > Settings > Developer settings)")

    steps, check = export.plan_steps(plan, n_steps=args.steps)
    print()
    print(report.format_steps(steps))
    vs_even = f", {baseline[1].total_time - check.sim.total_time:.0f} s faster than even pace" if baseline else ""
    status = "meets the reserves" if check.feasible else "DOES NOT meet the reserves"
    print(f"\nRiding these steps exactly: {report.format_duration(check.sim.total_time)} "
          f"({check.time_cost_s:+.0f} s vs the continuous plan{vs_even}); {status}"
          + (f" after lowering every step by {check.offset_w:.0f} W" if check.offset_w else ""))
    if not check.feasible:
        return 1

    if args.workout:
        args.workout.write_text(export.workout_text(steps) + "\n", encoding="utf-8")
        print(f"wrote {args.workout}")
    if args.export:
        payload = export.event_payload(plan, steps, check, day)
        if args.dry_run:
            print(json.dumps(payload, indent=2))
            return 0
        event = export.upload_intervals(payload, api_key, args.intervals_athlete)
        problems = export.verify_event(event, steps)
        print(f"uploaded '{payload['name']}' to intervals.icu for {day} (event {event.get('id', '?')})")
        if problems:
            print("intervals.icu parsed the workout differently than intended:", file=sys.stderr)
            for p in problems:
                print(f"  - {p}", file=sys.stderr)
            return 1
        print(f"verified: intervals.icu parsed all {len(steps)} steps with the intended distances and watts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
