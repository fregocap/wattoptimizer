import base64
import json
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from unittest import mock

import pytest

from wattoptimizer import web
from wattoptimizer.demo import write_demo_gpx


@pytest.fixture(scope="module")
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), web._Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1]
    httpd.shutdown()


def _request(port, method, path, body=None, host="127.0.0.1"):
    conn = HTTPConnection("127.0.0.1", port, timeout=300)
    conn.request(method, path, body=None if body is None else json.dumps(body), headers={"Host": host})
    r = conn.getresponse()
    data = r.read()
    return r.status, (json.loads(data) if r.getheader("Content-Type") == "application/json" else data)


def test_page_and_host_check(server):
    status, page = _request(server, "GET", "/")
    assert status == 200 and b"Make my plan" in page
    assert _request(server, "GET", "/api/status", host="evil.example")[0] == 403


def test_plan_then_push(server, tmp_path):
    gpx = write_demo_gpx(tmp_path / "demo.gpx")
    body = {
        "filename": "demo.gpx",
        "data": base64.b64encode(gpx.read_bytes()).decode(),
        "athlete": {"cp": "300", "w_prime": 20000, "rider_mass": "75"},
        "race": {"w_prime_final": 0.25},
        "wind": "calm",
        "steps": "8",
    }
    status, plan = _request(server, "POST", "/api/plan", body)
    assert status == 200, plan
    assert plan["feasible"] and len(plan["steps"]) == 8
    assert len(plan["series"]["km"]) == len(plan["series"]["power"]) + 1
    assert len(plan["series"]["lat"]) == len(plan["series"]["km"])
    assert len(plan["series"]["upright"]) == len(plan["series"]["head_wind"]) == len(plan["series"]["power"])
    assert plan["stepped_s"] >= plan["finish_s"] - 1.0

    sent = {}

    def fake_upload(payload, api_key, athlete_id="0", **kw):
        sent.update(payload=payload, key=api_key)
        steps = web._PLANS[plan["id"]][1]
        doc = [{"distance": round(s.length_m / 10) * 10, "power": {"start": s.low_w, "end": s.high_w, "units": "w"}}
               for s in steps]
        return {"id": 1, "workout_doc": {"steps": doc}}

    with mock.patch.object(web.export, "upload_intervals", fake_upload):
        status, out = _request(server, "POST", "/api/push",
                               {"id": plan["id"], "date": "2026-11-01", "name": "Test", "api_key": "k"})
    assert status == 200, out
    assert out["problems"] == [] and sent["key"] == "k" and sent["payload"]["name"] == "Test"


def test_bad_inputs_are_reported(server):
    status, out = _request(server, "POST", "/api/plan", {"filename": "x.txt", "data": ""})
    assert status == 400 and "gpx" in out["error"]
    status, out = _request(server, "POST", "/api/push", {"id": "nope", "date": "2026-11-01"})
    assert status == 400
