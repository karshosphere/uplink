import pytest
from fastapi import APIRouter, Depends

from uplink.auth import TokenError, read_token, require_token


@pytest.fixture
def guarded(app):
    router = APIRouter()

    @router.get("/guarded")
    def guarded_route():
        return {"ok": True}

    app.include_router(router, dependencies=[Depends(require_token)])


@pytest.mark.parametrize("headers", [
    {},
    {"Authorization": "test-token"},
    {"Authorization": "Basic test-token"},
    {"Authorization": "Bearer "},
    {"Authorization": "Bearer wrong"},
    {"Authorization": "Bearer test-token-and-more"},
])
def test_refuses_bad_tokens(client, guarded, headers):
    response = client.get("/guarded", headers=headers)
    assert response.status_code == 401
    assert response.json()["error"] == "unauthorised"


def test_accepts_correct_token(client, guarded, auth):
    response = client.get("/guarded", headers=auth)
    assert response.status_code == 200


def test_health_needs_no_token(client):
    assert client.get("/health").status_code == 200


def test_reads_token(tmp_path):
    path = tmp_path / "token"
    path.write_text("secret\n")
    path.chmod(0o600)
    assert read_token(path) == "secret"


def test_missing_token_file(tmp_path):
    with pytest.raises(TokenError, match="does not exist"):
        read_token(tmp_path / "token")


def test_loose_permissions(tmp_path):
    path = tmp_path / "token"
    path.write_text("secret")
    path.chmod(0o640)
    with pytest.raises(TokenError, match="chmod 600"):
        read_token(path)


def test_empty_token(tmp_path):
    path = tmp_path / "token"
    path.write_text("\n")
    path.chmod(0o600)
    with pytest.raises(TokenError, match="empty"):
        read_token(path)
