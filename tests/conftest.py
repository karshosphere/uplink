import pytest
from fastapi.testclient import TestClient

from uplink.app import create_app
from uplink.config import Config


@pytest.fixture
def config(tmp_path):
    projects = tmp_path / "Projects"
    projects.mkdir()
    return Config(
        projects_dir=projects,
        socket_path=tmp_path / "uplink.sock",
        token_file=tmp_path / "token",
        audit_log=tmp_path / "audit.log",
    )


TOKEN = "test-token"


@pytest.fixture
def auth():
    return {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def app(config):
    return create_app(config, TOKEN)


@pytest.fixture
def client(app):
    return TestClient(app)
