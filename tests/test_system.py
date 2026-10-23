import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from uplink import stats

BOOT = 1_700_000_000.0
GiB = 1024**3


def reading(label, current, high=None, critical=None):
    return SimpleNamespace(label=label, current=current, high=high, critical=critical)


def disk_usage(mount):
    if mount == "/missing":
        raise FileNotFoundError(2, "No such file or directory")
    return SimpleNamespace(total=100 * GiB, used=40 * GiB, free=60 * GiB, percent=40.0)


@pytest.fixture
def fake_psutil(monkeypatch):
    fake = SimpleNamespace(
        sensors_temperatures=lambda: {"coretemp": [reading("Package id 0", 48.0, 80.0, 100.0)]},
        virtual_memory=lambda: SimpleNamespace(total=16 * GiB, used=6 * GiB, available=10 * GiB, percent=37.5),
        swap_memory=lambda: SimpleNamespace(total=2 * GiB, used=0, free=2 * GiB, percent=0.0),
        cpu_percent=lambda interval, percpu: [10.0, 30.0],
        getloadavg=lambda: (0.5, 0.4, 0.3),
        cpu_count=lambda: 2,
        disk_usage=disk_usage,
        boot_time=lambda: BOOT,
    )
    monkeypatch.setattr(stats, "psutil", fake)
    return fake


def test_needs_token(client, fake_psutil):
    assert client.get("/system/stats").status_code == 401


def test_temps(client, auth, fake_psutil):
    assert client.get("/system/temps", headers=auth).json() == {
        "sensors": {"coretemp": [{"label": "Package id 0", "current": 48.0, "high": 80.0, "critical": 100.0}]}
    }


def test_temps_without_sensor_support(client, auth, fake_psutil):
    del fake_psutil.sensors_temperatures
    assert client.get("/system/temps", headers=auth).json() == {"sensors": {}}


def test_memory(client, auth, fake_psutil):
    body = client.get("/system/memory", headers=auth).json()
    assert body["ram"] == {"total": 16 * GiB, "used": 6 * GiB, "available": 10 * GiB, "percent": 37.5}
    assert body["swap"]["free"] == 2 * GiB


def test_cpu(client, auth, fake_psutil):
    assert client.get("/system/cpu", headers=auth).json() == {
        "count": 2,
        "percent": 20.0,
        "per_core": [10.0, 30.0],
        "load_average": {"1m": 0.5, "5m": 0.4, "15m": 0.3},
    }


def test_disk_uses_configured_mounts(app, client, auth, fake_psutil, config):
    app.state.config = replace(config, disk_mounts=("/", "/missing"))
    mounts = client.get("/system/disk", headers=auth).json()["mounts"]
    assert mounts[0] == {"mount": "/", "total": 100 * GiB, "used": 40 * GiB, "free": 60 * GiB, "percent": 40.0}
    assert mounts[1] == {"mount": "/missing", "error": "No such file or directory"}


def test_uptime(client, auth, fake_psutil):
    body = client.get("/system/uptime", headers=auth).json()
    assert body["boot_time"] == "2023-11-14T22:13:20+00:00"
    assert abs(body["uptime_seconds"] - (time.time() - BOOT)) < 5


def test_stats_has_everything(client, auth, fake_psutil):
    body = client.get("/system/stats", headers=auth).json()
    assert set(body) == {"temps", "memory", "cpu", "disk", "uptime"}


def test_real_psutil_answers(client, auth):
    body = client.get("/system/stats", headers=auth).json()
    assert body["memory"]["ram"]["total"] > 0
    assert body["cpu"]["count"] > 0
