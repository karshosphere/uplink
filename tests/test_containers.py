import json

import pytest
from docker.errors import APIError, DockerException

from fake_docker import FakeDocker
from uplink.docker_ops import Containers


@pytest.fixture
def fake(app, config):
    fake = FakeDocker()
    projects = config.projects_dir
    fake.add("judy", "running", str(projects / ".devcontainer"))
    fake.add("judy-devcontainer", "running", str(projects / "Judy" / ".devcontainer"))
    fake.add("blog-dev", "exited", str(projects / "blog" / ".devcontainer"))
    fake.add("shop-dev", "running", str(projects / "shop" / ".devcontainer"))
    fake.add("postgres", "running", "/srv/postgres")
    fake.add("loose", "exited")
    app.state.containers = Containers(lambda: fake, config)
    return fake


def test_needs_token(client, fake):
    assert client.get("/containers").status_code == 401
    assert client.post("/containers/blog-dev/start").status_code == 401
    assert fake.calls == []


def test_lists_every_container(client, auth, fake):
    listed = {c["name"]: c for c in client.get("/containers", headers=auth).json()["containers"]}
    assert sorted(listed) == ["blog-dev", "judy", "judy-devcontainer", "loose", "postgres", "shop-dev"]
    assert listed["blog-dev"] == {
        "name": "blog-dev", "image": "img:latest", "state": "exited", "status": "Exited (0)",
        "project": "blog", "controllable": True,
    }
    assert listed["judy"]["controllable"] is False
    assert listed["judy-devcontainer"]["project"] == "Judy"
    assert listed["judy-devcontainer"]["controllable"] is False
    assert listed["postgres"]["project"] is None
    assert listed["loose"]["project"] is None


def test_gets_one(client, auth, fake):
    body = client.get("/containers/shop-dev", headers=auth).json()
    assert body["name"] == "shop-dev"
    assert body["state"] == "running"
    assert body["project"] == "shop"
    assert len(body["id"]) == 12


def test_unknown_container(client, auth, fake):
    response = client.get("/containers/ghost", headers=auth)
    assert response.status_code == 404
    assert response.json()["error"] == "container_not_found"


@pytest.mark.parametrize("name", ["-rf", "a b", "a;b", "%2e%2e"])
def test_invalid_name(client, auth, fake, name):
    response = client.get(f"/containers/{name}", headers=auth)
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_name"


@pytest.mark.parametrize("action, before, after", [
    ("start", "blog-dev", "running"),
    ("stop", "shop-dev", "exited"),
    ("restart", "shop-dev", "running"),
])
def test_acts_on_project_containers(client, auth, fake, action, before, after):
    response = client.post(f"/containers/{before}/{action}", headers=auth)
    assert response.status_code == 200
    assert response.json()["state"] == after
    assert fake.calls == [(action, before)]


def test_refuses_protected(client, auth, fake):
    response = client.post("/containers/judy/stop", headers=auth)
    assert response.status_code == 403
    assert response.json()["error"] == "protected"
    assert fake.calls == []


def test_refuses_containers_of_protected_projects(client, auth, fake):
    response = client.post("/containers/judy-devcontainer/stop", headers=auth)
    assert response.status_code == 403
    assert response.json()["error"] == "protected"
    assert fake.calls == []


def test_refuses_protected_by_id(client, auth, fake):
    judy_id = fake.by_name["judy"]["Id"][:12]
    response = client.post(f"/containers/{judy_id}/stop", headers=auth)
    assert response.status_code == 403
    assert fake.calls == []


@pytest.mark.parametrize("name", ["postgres", "loose"])
def test_refuses_containers_outside_projects(client, auth, fake, name):
    response = client.post(f"/containers/{name}/stop", headers=auth)
    assert response.status_code == 403
    assert response.json()["error"] == "not_a_project"
    assert fake.calls == []


def test_refuses_projects_dir_itself(client, auth, fake, app, config):
    fake.add("odd", "running", str(config.projects_dir))
    assert client.post("/containers/odd/stop", headers=auth).json()["error"] == "not_a_project"


def test_refuses_lookalike_dir(client, auth, fake, config):
    fake.add("evil", "running", str(config.projects_dir) + "-evil/x")
    assert client.post("/containers/evil/stop", headers=auth).json()["error"] == "not_a_project"


def test_refuses_dotdot_out_of_projects(client, auth, fake, config):
    fake.add("climber", "running", str(config.projects_dir / "blog" / ".." / ".." / "elsewhere"))
    assert client.post("/containers/climber/stop", headers=auth).json()["error"] == "not_a_project"


def test_refuses_symlink_out_of_projects(client, auth, fake, config, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (config.projects_dir / "sneaky").symlink_to(outside)
    fake.add("sneaky-dev", "running", str(config.projects_dir / "sneaky"))
    assert client.post("/containers/sneaky-dev/stop", headers=auth).json()["error"] == "not_a_project"


def test_docker_error(client, auth, fake):
    fake.fail_with = APIError("boom", explanation="cannot start")
    response = client.post("/containers/blog-dev/start", headers=auth)
    assert response.status_code == 502
    assert response.json() == {"error": "docker_error", "detail": "cannot start"}


def test_docker_unavailable(client, auth, app, config):
    def refuse():
        raise DockerException("socket not found")

    app.state.containers = Containers(refuse, config)
    response = client.get("/containers", headers=auth)
    assert response.status_code == 503
    assert response.json()["error"] == "docker_unavailable"


def test_audits_actions(client, auth, fake, config):
    client.post("/containers/blog-dev/start", headers=auth)
    client.post("/containers/judy/stop", headers=auth)
    started, refused = [json.loads(line) for line in config.audit_log.read_text().splitlines()]
    assert (started["action"], started["container"], started["state"]) == ("start", "blog-dev", "running")
    assert refused["action"] == "stop"
    assert refused["outcome"] == 403
