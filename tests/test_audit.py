import json
import stat

from fastapi import Depends, Request

from uplink import audit
from uplink.auth import require_token


def records(config):
    return [json.loads(line) for line in config.audit_log.read_text().splitlines()]


def test_logs_each_request(client, config):
    client.get("/health")
    client.get("/nowhere")
    first, second = records(config)
    assert first["method"] == "GET"
    assert first["path"] == "/health"
    assert first["outcome"] == 200
    assert isinstance(first["duration_ms"], int)
    assert "time" in first
    assert second["outcome"] == 404


def test_logs_refused_requests(app, client, config):
    @app.get("/guarded", dependencies=[Depends(require_token)])
    def guarded():
        return {}

    client.get("/guarded", headers={"Authorization": "Bearer wrong"})
    assert records(config)[0]["outcome"] == 401


def test_log_is_private(client, config):
    client.get("/health")
    assert stat.S_IMODE(config.audit_log.stat().st_mode) == 0o600


def test_routes_can_add_fields(app, client, config):
    @app.post("/noted")
    def noted(request: Request):
        audit.note(request, command=["d", "up"], exit_code=0)
        return {}

    client.post("/noted")
    record = records(config)[0]
    assert record["command"] == ["d", "up"]
    assert record["exit_code"] == 0


def test_also_writes_stdout(client, capsys):
    client.get("/health")
    assert '"path": "/health"' in capsys.readouterr().out


def test_never_logs_the_token(client, config, auth):
    client.get("/health", headers=auth)
    assert "test-token" not in config.audit_log.read_text()
