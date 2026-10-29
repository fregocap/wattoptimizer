import numpy as np

from wattoptimizer import estimate


def test_fit_recovers_cp_and_w_prime():
    t = np.array(estimate.FIT_DURATIONS)
    cp, w_prime = estimate.fit_cp(t, 250 + 18_000 / t)
    assert abs(cp - 250) < 1e-6 and abs(w_prime - 18_000) < 1e-3


def test_fit_rejects_efforts_that_are_not_all_out():
    t = np.array(estimate.FIT_DURATIONS)
    assert estimate.fit_cp(t, np.full(len(t), 200.0)) is None  # flat curve: W' of zero
    assert estimate.fit_cp([300, 360], [300, 290]) is None  # too few, too close


def test_mean_max_finds_the_best_block():
    power = np.full(3600, 150.0)
    power[1000:1300] = 320.0  # a 5 min effort
    best = dict(estimate.mean_max(power, durations=(300, 600)))
    assert best[300] == 320.0
    assert abs(best[600] - (300 * 320 + 300 * 150) / 600) < 1e-9


def test_intervals_falls_back_to_ftp(monkeypatch):
    def fake_get(path, api_key, timeout=30.0):
        if path.startswith("/athlete/0/power-curves"):
            return {"list": [], "activities": {}}
        return {"icu_weight": 70.0, "sportSettings": [{"types": ["Ride"], "ftp": 167}]}

    monkeypatch.setattr(estimate.export, "intervals_get", fake_get)
    est = estimate.from_intervals("k")
    assert est.cp == 167 and est.w_prime == estimate.TYPICAL_W_PRIME and est.weight == 70.0
    assert "FTP" in est.note


def test_intervals_fits_the_power_curve(monkeypatch):
    secs = [5, 60, 120, 180, 300, 480, 720, 1200, 3600]
    watts = [700, 400] + [round(240 + 20_000 / s) for s in secs[2:-1]] + [230]

    def fake_get(path, api_key, timeout=30.0):
        if path.startswith("/athlete/0/power-curves"):
            return {"list": [{"secs": secs, "watts": watts}]}
        return {"icu_weight": 68.0, "sportSettings": [{"types": ["Ride"], "ftp": 250}]}

    monkeypatch.setattr(estimate.export, "intervals_get", fake_get)
    est = estimate.from_intervals("k")
    assert abs(est.cp - 240) < 2 and abs(est.w_prime - 20_000) < 600
    assert len(est.efforts) == len(estimate.FIT_DURATIONS)


def _fit_crc(data: bytes, crc: int = 0) -> int:
    table = [0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
             0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400]
    for byte in data:
        for nibble in (byte & 0xF, byte >> 4):
            tmp = table[crc & 0xF]
            crc = ((crc >> 4) & 0x0FFF) ^ tmp ^ table[nibble]
    return crc


def _write_power_fit(path, power):
    """Minimal FIT file: one definition and a 1 Hz 'record' (timestamp, power) per sample."""
    import struct

    records = bytearray()
    # definition: local 0, little endian, global message 20 (record), fields 253 timestamp u32 and 7 power u16
    records += bytes([0x40, 0, 0]) + struct.pack("<H", 20) + bytes([2, 253, 4, 0x86, 7, 2, 0x84])
    for i, w in enumerate(power):
        records += bytes([0x00]) + struct.pack("<IH", 1_000_000_000 + i, int(w))
    header = struct.pack("<BBHI4s", 14, 0x20, 2132, len(records), b".FIT")
    header += struct.pack("<H", _fit_crc(header))
    body = header + bytes(records)
    path.write_bytes(body + struct.pack("<H", _fit_crc(body)))


def test_ride_file_estimate(tmp_path):
    power = np.full(10_000, 140.0)
    start = 200
    for d in estimate.FIT_DURATIONS:  # all-out efforts on CP 230 W, W' 16 kJ, far enough apart not to merge
        power[start:start + d] = round(230 + 16_000 / d)
        start += d + 1200
    path = tmp_path / "ride.fit"
    _write_power_fit(path, power)
    est = estimate.from_ride(path)
    assert abs(est.cp - 230) < 3 and abs(est.w_prime - 16_000) < 1_000, est
