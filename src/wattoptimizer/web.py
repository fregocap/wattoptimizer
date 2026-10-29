"""Local web app: drop a course file, enter your numbers, get the plan, push it to intervals.icu.

``wattopt serve`` starts it on http://127.0.0.1:8765. It only listens on localhost:
the solver runs on your machine and your intervals.icu key never leaves it except
to go to intervals.icu.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import sys
import tempfile
import threading
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from urllib.error import HTTPError

import numpy as np

from . import estimate, export, report
from .athlete import Athlete, RaceSpec
from .course import load_course
from .optimizer import InfeasibleError, optimize
from .simulate import best_even_power
from .wind import ConstantWind, fetch_open_meteo

MAX_UPLOAD = 20 * 1024 * 1024
ATHLETE_FIELDS = ("rider_mass", "bike_mass", "cp", "w_prime", "cda_aero", "cda_climb", "crr", "glycogen", "cho_intake")
RACE_FIELDS = ("w_prime_final", "glycogen_final")
_PLANS: dict[str, tuple] = {}  # plan id -> (plan, steps, check); kept for the push
_LOCK = threading.Lock()


def make_plan(body: dict) -> dict:
    """Solve the plan described by a request body; returns the JSON the page renders."""
    name = Path(body.get("filename") or "course.gpx").name
    suffix = Path(name).suffix.lower()
    if suffix not in (".gpx", ".fit"):
        raise ValueError("drop a .gpx or .fit file")
    data = base64.b64decode(body["data"])

    athlete, race = Athlete(), RaceSpec()
    for key in ATHLETE_FIELDS:
        if body.get("athlete", {}).get(key) not in (None, ""):
            setattr(athlete, key, float(body["athlete"][key]))
    for key in RACE_FIELDS:
        if body.get("race", {}).get(key) not in (None, ""):
            setattr(race, key, float(body["race"][key]))
    if body.get("start"):
        race.start_time = datetime.fromisoformat(body["start"])
        if race.start_time.tzinfo is None:
            race.start_time = race.start_time.replace(tzinfo=timezone.utc)

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / name  # so a file without a track name is named after itself
        path.write_bytes(data)
        course = load_course(path)

    field, wind_note = None, "calm air"
    wind = body.get("wind", "calm")
    if wind == "forecast":
        if race.start_time is None:
            raise ValueError("the wind forecast needs a start date and time")
        duration = timedelta(seconds=course.total_distance / 5.0)
        field = fetch_open_meteo(course, race.start_time, duration)
        t0 = race.start_time.timestamp()
        temperature = field.mean_temperature(t0, t0 + duration.total_seconds())
        if temperature is not None:
            race.temperature = temperature
        wind_note = "Open-Meteo hourly wind along the course"
    elif wind == "constant":
        speed, direction = float(body.get("wind_speed") or 0), float(body.get("wind_from") or 0)
        field = ConstantWind(speed, direction)
        wind_note = f"{speed:.1f} m/s from {direction:.0f}°"

    plan = optimize(course, athlete, race, wind=field)
    baseline = best_even_power(
        course, athlete, race, head_wind=plan.head_wind, cda=plan.cda, cp=plan.cp_segment, v_max=plan.v_max
    )
    steps, check = export.plan_steps(plan, n_steps=int(body.get("steps") or 20))

    plan_id = secrets.token_urlsafe(8)
    with _LOCK:
        _PLANS.clear()  # one user, one plan at a time
        _PLANS[plan_id] = (plan, steps, check)

    stepped = export.stepped_power(plan, steps)
    km = plan.course.distance / 1000.0
    return {
        "id": plan_id,
        "name": course.name,
        "wind": wind_note,
        "summary": report.summary(plan, baseline),
        "finish_s": plan.total_time,
        "stepped_s": check.sim.total_time,
        "even_s": baseline[1].total_time if baseline else None,  # None: no constant power meets the reserves
        "even_w": baseline[0] if baseline else None,
        "avg_w": plan.avg_power,
        "distance_km": course.total_distance / 1000.0,
        "gain_m": course.elevation_gain,
        "cp_w": athlete.cp,
        "targets": {"w_bal_kj": race.w_prime_final * athlete.w_prime / 1000.0, "glycogen": race.glycogen_final},
        "feasible": bool(check.feasible),
        "offset_w": check.offset_w,
        "series": {
            "km": _round(km, 3),
            "elevation": _round(course.elevation, 1),
            "power": _round(plan.power, 0),
            "stepped": _round(stepped, 0),
            "cp": _round(plan.cp_effective, 0),
            "speed": _round(3.6 * plan.speed, 1),
            "w_bal": _round(plan.w_bal / 1000.0, 2),
            "glycogen": _round(plan.glycogen, 0),
            # for the map: node coordinates, then per-segment position and wind
            "lat": _round(course.lat, 5),
            "lon": _round(course.lon, 5),
            "upright": [bool(c) for c in plan.climbing],
            "head_wind": _round(plan.head_wind, 1),
        },
        "steps": [
            {"start_km": s.start_m / 1000, "end_km": s.end_m / 1000, "low": s.low_w, "high": s.high_w,
             "target": s.target_w, "upright": s.upright}
            for s in steps
        ],
        "workout": export.workout_text(steps),
    }


def _estimate_json(est: estimate.Estimate) -> dict:
    return {"cp": est.cp, "w_prime": est.w_prime, "weight": est.weight, "ftp": est.ftp,
            "efforts": [{"secs": t, "watts": round(w)} for t, w in est.efforts], "note": est.note}


def estimate_from_intervals(body: dict) -> dict:
    api_key = (body.get("api_key") or "").strip() or os.environ.get("INTERVALS_API_KEY")
    if not api_key:
        raise ValueError("enter your intervals.icu API key (Settings > Developer settings)")
    return _estimate_json(estimate.from_intervals(api_key))


def estimate_from_ride(body: dict) -> dict:
    name = Path(body.get("filename") or "ride.fit").name
    if Path(name).suffix.lower() != ".fit":
        raise ValueError("drop a .fit ride file with power data")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / name
        path.write_bytes(base64.b64decode(body["data"]))
        return _estimate_json(estimate.from_ride(path))


def push_plan(body: dict) -> dict:
    with _LOCK:
        stored = _PLANS.get(body.get("id", ""))
    if stored is None:
        raise ValueError("that plan is gone (the server restarted or a newer plan replaced it); plan again")
    plan, steps, check = stored
    if not check.feasible:
        raise ValueError("the stepped plan does not meet the reserves; not uploading it")
    api_key = (body.get("api_key") or "").strip() or os.environ.get("INTERVALS_API_KEY")
    if not api_key:
        raise ValueError("enter your intervals.icu API key (Settings > Developer settings)")
    day = date.fromisoformat(body["date"])
    payload = export.event_payload(plan, steps, check, day, name=(body.get("name") or None))
    event = export.upload_intervals(payload, api_key)
    return {"event_id": event.get("id"), "date": day.isoformat(), "name": payload["name"],
            "problems": export.verify_event(event, steps), "steps": len(steps)}


def _round(values, digits: int) -> list:
    return [round(float(v), digits) for v in np.asarray(values)]


class _Handler(BaseHTTPRequestHandler):
    server_version = "wattoptimizer"

    def do_GET(self):
        if not self._local():
            return
        if self.path in ("/", "/index.html"):
            page = files("wattoptimizer").joinpath("web/index.html").read_bytes()
            self._send(200, page, "text/html; charset=utf-8")
        elif self.path == "/api/status":
            self._json(200, {"env_key": bool(os.environ.get("INTERVALS_API_KEY"))})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if not self._local():
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_UPLOAD:
            self._json(413, {"error": "file too large"})
            return
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            if self.path == "/api/plan":
                self._json(200, make_plan(body))
            elif self.path == "/api/push":
                self._json(200, push_plan(body))
            elif self.path == "/api/estimate/intervals":
                self._json(200, estimate_from_intervals(body))
            elif self.path == "/api/estimate/ride":
                self._json(200, estimate_from_ride(body))
            else:
                self._json(404, {"error": "not found"})
        except HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            self._json(502, {"error": f"intervals.icu answered {exc.code} {exc.reason}: {detail}"})
        except (InfeasibleError, ValueError, KeyError, OSError) as exc:
            self._json(400, {"error": str(exc)})
        except Exception as exc:  # keep the page informed instead of dropping the connection
            self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

    def _local(self) -> bool:
        # refuse requests whose Host is not this machine (DNS rebinding)
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        if host not in ("127.0.0.1", "localhost", "[::1]"):
            self._json(403, {"error": "forbidden"})
            return False
        return True

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _send(self, code: int, data: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        sys.stderr.write(f"{self.command} {self.path} -> {args[1] if len(args) > 1 else ''}\n")


def serve(port: int = 8765, open_browser: bool = True) -> None:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"WattOptimizer running at {url}  (Ctrl+C to stop)")
    if open_browser:
        import webbrowser

        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
