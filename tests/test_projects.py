import json
import subprocess
from types import SimpleNamespace

import pytest

from fake_docker import FakeDocker
from uplink import projects as projects_module
from uplink.docker_ops import Containers
from uplink.errors import UplinkError
from uplink.projects import Projects


def make_project(root, name, compose=True):
    path = root / name
    (path / ".devcontainer").mkdir(parents=True)
    (path / "main").mkdir()
    if compose:
        (path / ".devcontainer" / "compose.yaml").write_text("services: {}\n")
    return path


@pytest.fixture
def root(config):
    return config.projects_dir


@pytest.fixture
def fake(app, config, root):
    make_project(root, "blog")
    make_project(root, "shop")
    make_project(root, "notes", compose=False)
    (root / ".devcontainer").mkdir()
    (root / ".devcontainer" / "compose.yaml").write_text("services: {}\n")
    (root / "README.md").write_text("not a project")

    fake = FakeDocker()
    fake.add("judy", "running", str(root / ".devcontainer"))
    fake.add("blog-devcontainer", "running", str(root / "blog" / ".devcontainer"))
    fake.add("blog-db", "running", str(root / "blog" / ".devcontainer"))
    fake.add("shop-devcontainer", "exited", str(root / "shop" / ".devcontainer"))
    app.state.containers = Containers(lambda: fake, config.projects_dir, config.protected)
    app.state.projects = Projects(config, app.state.containers)
    return fake


@pytest.fixture
def runs(monkeypatch):
    calls = []

    def run(command, cwd, timeout, **kwargs):
        calls.append({"command": command, "cwd": cwd, "timeout": timeout, **kwargs})
        return SimpleNamespace(returncode=0, stdout="started\n", stderr="")

    monkeypatch.setattr(projects_module.shutil, "which", lambda name: f"/usr/local/bin/{name}")
    monkeypatch.setattr(projects_module.subprocess, "run", run)
    return calls


def test_needs_token(client, fake, runs):
    assert client.get("/projects").status_code == 401
    assert client.post("/projects/blog/up").status_code == 401
    assert runs == []


def test_lists_projects_with_containers(client, auth, fake, root):
    listed = client.get("/projects", headers=auth).json()["projects"]
    assert [p["name"] for p in listed] == ["blog", "shop"]
    blog, shop = listed
    assert blog["path"] == str((root / "blog").resolve())
    assert sorted(c["name"] for c in blog["containers"]) == ["blog-db", "blog-devcontainer"]
    assert shop["containers"] == [{"name": "shop-devcontainer", "state": "exited", "status": "Exited (0)"}]


def test_lists_nothing_without_projects_dir(client, auth, fake, root):
    fake.by_name.clear()
    for child in sorted(root.rglob("*"), reverse=True):
        child.rmdir() if child.is_dir() else child.unlink()
    root.rmdir()
    assert client.get("/projects", headers=auth).json() == {"projects": []}


@pytest.mark.parametrize("action", ["up", "down"])
def test_runs_d_in_the_project(client, auth, fake, runs, root, config, action):
    response = client.post(f"/projects/blog/{action}", headers=auth)
    assert response.status_code == 200
    assert response.json() == {
        "command": ["/usr/local/bin/d", action], "exit_code": 0, "timed_out": False,
        "stdout": "started\n", "stderr": "",
    }
    (call,) = runs
    assert call["cwd"] == (root / "blog").resolve()
    assert call["timeout"] == config.command_timeout_seconds
    assert call["stdin"] == subprocess.DEVNULL


def test_reports_failures(client, auth, fake, monkeypatch, runs):
    monkeypatch.setattr(projects_module.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="no such service\n"))
    body = client.post("/projects/blog/up", headers=auth).json()
    assert body["exit_code"] == 1
    assert body["stderr"] == "no such service\n"


def test_reports_timeouts(client, auth, fake, monkeypatch, runs):
    def slow(command, timeout, **kwargs):
        raise subprocess.TimeoutExpired(command, timeout, output=b"building...", stderr=None)

    monkeypatch.setattr(projects_module.subprocess, "run", slow)
    body = client.post("/projects/blog/up", headers=auth).json()
    assert body["timed_out"] is True
    assert body["exit_code"] is None
    assert body["stdout"] == "building..."


def test_trims_long_output(client, auth, fake, monkeypatch, runs):
    monkeypatch.setattr(projects_module.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(returncode=0, stdout="x" * 100_000 + "end", stderr=""))
    stdout = client.post("/projects/blog/up", headers=auth).json()["stdout"]
    assert len(stdout) == projects_module.OUTPUT_LIMIT
    assert stdout.endswith("end")


def test_refuses_a_second_action_while_one_runs(client, auth, fake, runs, app):
    lock = app.state.projects._locks["blog"]
    lock.acquire()
    try:
        response = client.post("/projects/blog/down", headers=auth)
    finally:
        lock.release()
    assert response.status_code == 409
    assert response.json()["error"] == "busy"
    assert runs == []


def test_missing_d(client, auth, fake, monkeypatch, runs):
    monkeypatch.setattr(projects_module.shutil, "which", lambda name: None)
    response = client.post("/projects/blog/up", headers=auth)
    assert response.status_code == 503
    assert response.json()["error"] == "d_unavailable"


def test_audits_d(client, auth, fake, runs, config):
    client.post("/projects/blog/up", headers=auth)
    record = json.loads(config.audit_log.read_text().splitlines()[-1])
    assert record["project"] == "blog"
    assert record["command"] == ["/usr/local/bin/d", "up"]
    assert record["exit_code"] == 0


@pytest.mark.parametrize("name", [".devcontainer", "-rf", "a b", "a;b", "$(id)", ".hidden"])
def test_refuses_invalid_names(client, auth, fake, runs, name):
    response = client.post(f"/projects/{name}/up", headers=auth)
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_name"
    assert runs == []


@pytest.mark.parametrize("path", ["/projects/../up", "/projects/%2e%2e/up", "/projects/..%2Fetc/up",
                                  "/projects/%2Fetc/up"])
def test_refuses_traversal(client, auth, fake, runs, path):
    response = client.post(path, headers=auth)
    assert response.status_code in {400, 404, 405}
    assert runs == []


def test_unit_refuses_dotdot_and_absolute(app, fake):
    for name in ["..", ".", "/etc", "../blog", "blog/../shop"]:
        with pytest.raises(UplinkError) as e:
            app.state.projects.resolve(name)
        assert e.value.code == "invalid_name"


def test_refuses_symlink_out_of_projects(client, auth, fake, runs, root, tmp_path):
    outside = make_project(tmp_path, "outside")
    (root / "escape").symlink_to(outside)
    response = client.post("/projects/escape/up", headers=auth)
    assert response.status_code == 403
    assert response.json()["error"] == "outside_projects"
    assert runs == []


def test_refuses_symlink_to_another_project(client, auth, fake, runs, root):
    (root / "alias").symlink_to(root / "blog")
    assert client.post("/projects/alias/up", headers=auth).json()["error"] == "outside_projects"


def test_refuses_missing_compose_file(client, auth, fake, runs):
    response = client.post("/projects/notes/up", headers=auth)
    assert response.status_code == 404
    assert response.json()["error"] == "project_not_found"
    assert runs == []


def test_unknown_project(client, auth, fake, runs):
    assert client.post("/projects/ghost/up", headers=auth).json()["error"] == "project_not_found"
