import stat

from uplink import __version__
from uplink.server import bind_unix_socket


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True, "version": __version__}


def test_unknown_route_uses_error_shape(client):
    response = client.get("/nowhere")
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


def test_wrong_method_uses_error_shape(client):
    response = client.post("/health")
    assert response.status_code == 405
    assert response.json()["error"] == "method_not_allowed"


def test_socket_is_group_only(tmp_path):
    path = tmp_path / "state" / "uplink.sock"
    sock = bind_unix_socket(path)
    try:
        mode = path.stat().st_mode
        assert stat.S_ISSOCK(mode)
        assert stat.S_IMODE(mode) == 0o660
    finally:
        sock.close()


def test_stale_socket_is_replaced(tmp_path):
    path = tmp_path / "uplink.sock"
    path.write_text("left over")
    sock = bind_unix_socket(path)
    try:
        assert stat.S_ISSOCK(path.stat().st_mode)
    finally:
        sock.close()
