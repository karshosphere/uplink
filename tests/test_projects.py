import json
import subprocess
from types import SimpleNamespace

import pytest

from tests.devenv import make_env, make_project
from tests.fake_docker import FakeDocker
from uplink import projects as projects_module
from uplink.docker_ops import Containers
from uplink.errors import UplinkError
from uplink.projects import Projects


@pytest.fixture
def root(config):
    return config.projects_dir


@pytest.fixture
def fake(app, config, root):
    make_project(config, "blog")
    make_project(config, "shop")
    make_project(config, "notes", compose=False)
    make_project(config, "Judy")
    make_env(config, "gone", project_root=root / "gone")
    (root / "README.md").write_text("not a project")

    envs = config.envs_dir
    fake = FakeDocker()
    fake.add("judy", "running", str(envs))
    fake.add("judy-devcontainer", "running", str(envs / "judy"))
    fake.add("blog-devcontainer", "running", str(envs / "blog"))
    fake.add("blog-db", "running", str(envs / "blog"))
    fake.add("shop-devcontainer", "exited", str(envs / "shop"))
    app.state.containers = Containers(lambda: fake, config)
    app.state.projects = Projects(config, app.state.containers)
    return fake


@pytest.fixture
def active_units():
    """Remote control units the fake systemd reports as running."""
    return set()


@pytest.fixture
def runs(monkeypatch, active_units):
    calls = []

    def run(command, cwd=None, timeout=None, **kwargs):
        calls.append({"command": command, "cwd": cwd, "timeout": timeout, **kwargs})
        if command[1:3] == ["--user", "is-active"]:
            return SimpleNamespace(
                returncode=0 if command[-1] in active_units else 3,
                stdout="",
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="started\n", stderr="")

    monkeypatch.setattr(
        projects_module.shutil, "which", lambda name: f"/usr/local/bin/{name}"
    )
    monkeypatch.setattr(projects_module.subprocess, "run", run)
    return calls


def test_needs_token(client, fake, runs):
    assert client.get("/projects").status_code == 401
    assert client.post("/projects/blog/up").status_code == 401
    assert runs == []


def test_lists_projects_with_containers(client, auth, fake, root):
    listed = client.get("/projects", headers=auth).json()["projects"]
    assert [p["name"] for p in listed] == ["Judy", "blog", "shop"]
    judy, blog, shop = listed
    assert judy["protected"] is True
    assert blog["protected"] is False
    assert blog["path"] == str((root / "blog").resolve())
    assert sorted(c["name"] for c in blog["containers"]) == [
        "blog-db",
        "blog-devcontainer",
    ]
    assert shop["containers"] == [
        {"name": "shop-devcontainer", "state": "exited", "status": "Exited (0)"}
    ]


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
        "command": ["/usr/local/bin/d", action],
        "exit_code": 0,
        "timed_out": False,
        "stdout": "started\n",
        "stderr": "",
    }
    (call,) = runs
    assert call["cwd"] == (root / "blog").resolve()
    assert call["timeout"] == config.command_timeout_seconds
    assert call["stdin"] == subprocess.DEVNULL


@pytest.mark.parametrize("action", ["up", "down"])
def test_refuses_protected_projects(client, auth, fake, runs, action):
    response = client.post(f"/projects/Judy/{action}", headers=auth)
    assert response.status_code == 403
    assert response.json()["error"] == "protected"
    assert runs == []


def test_reports_failures(client, auth, fake, monkeypatch, runs):
    monkeypatch.setattr(
        projects_module.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            returncode=1, stdout="", stderr="no such service\n"
        ),
    )
    body = client.post("/projects/blog/up", headers=auth).json()
    assert body["exit_code"] == 1
    assert body["stderr"] == "no such service\n"


def test_reports_timeouts(client, auth, fake, monkeypatch, runs):
    def slow(command, timeout, **kwargs):
        raise subprocess.TimeoutExpired(
            command, timeout, output=b"building...", stderr=None
        )

    monkeypatch.setattr(projects_module.subprocess, "run", slow)
    body = client.post("/projects/blog/up", headers=auth).json()
    assert body["timed_out"] is True
    assert body["exit_code"] is None
    assert body["stdout"] == "building..."


def test_trims_long_output(client, auth, fake, monkeypatch, runs):
    monkeypatch.setattr(
        projects_module.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            returncode=0, stdout="x" * 100_000 + "end", stderr=""
        ),
    )
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


def commands(runs):
    return [call["command"] for call in runs]


SYSTEMD_RUN_BLOG = [
    "/usr/local/bin/systemd-run",
    "--user",
    "--unit=uplink-remote-blog",
    "--collect",
    "--quiet",
]


def test_restart_restarts_then_starts_remote_control(client, auth, fake, runs, root):
    response = client.post("/projects/blog/restart", headers=auth)
    assert response.status_code == 200
    body = response.json()
    assert body["exit_code"] == 0
    assert body["remote"] == {"unit": "uplink-remote-blog", "status": "started"}
    stop, restart, remote = commands(runs)
    assert stop == ["/usr/local/bin/systemctl", "--user", "stop", "uplink-remote-blog"]
    assert restart == ["/usr/local/bin/d", "restart"]
    assert runs[1]["cwd"] == (root / "blog").resolve()
    assert remote[:5] == SYSTEMD_RUN_BLOG
    assert remote[5] == f"--working-directory={(root / 'blog').resolve()}"
    assert remote[-3:] == ["--", "/usr/local/bin/d", "cr"]


def test_restart_skips_remote_control_when_restart_fails(
    client, auth, fake, monkeypatch, runs
):
    def run(command, **kwargs):
        runs.append({"command": command, **kwargs})
        return SimpleNamespace(returncode=1, stdout="", stderr="no such service\n")

    monkeypatch.setattr(projects_module.subprocess, "run", run)
    body = client.post("/projects/blog/restart", headers=auth).json()
    assert body["exit_code"] == 1
    assert body["remote"] is None
    assert not any("systemd-run" in c[0] for c in commands(runs))


def test_ensure_running_starts_a_stopped_project(client, auth, fake, runs):
    body = client.post("/projects/shop/ensure-running", headers=auth).json()
    assert body["started"] is True
    assert body["up"]["command"] == ["/usr/local/bin/d", "up"]
    assert body["up"]["exit_code"] == 0
    assert body["remote"] == {"unit": "uplink-remote-shop", "status": "started"}
    assert [c[:3] for c in commands(runs)] == [
        ["/usr/local/bin/d", "up"],
        ["/usr/local/bin/systemctl", "--user", "is-active"],
        ["/usr/local/bin/systemd-run", "--user", "--unit=uplink-remote-shop"],
    ]


def test_ensure_running_leaves_a_running_project_up(client, auth, fake, runs):
    body = client.post("/projects/blog/ensure-running", headers=auth).json()
    assert body["started"] is False
    assert body["up"] is None
    assert body["remote"]["status"] == "started"
    assert ["/usr/local/bin/d", "up"] not in commands(runs)


def test_ensure_running_keeps_a_running_remote_session(
    client, auth, fake, runs, active_units
):
    active_units.add("uplink-remote-blog")
    body = client.post("/projects/blog/ensure-running", headers=auth).json()
    assert body["remote"] == {"unit": "uplink-remote-blog", "status": "already_running"}
    assert not any("systemd-run" in c[0] for c in commands(runs))


def test_ensure_running_stops_when_up_fails(client, auth, fake, monkeypatch, runs):
    def run(command, **kwargs):
        runs.append({"command": command, **kwargs})
        return SimpleNamespace(returncode=1, stdout="", stderr="build failed\n")

    monkeypatch.setattr(projects_module.subprocess, "run", run)
    body = client.post("/projects/shop/ensure-running", headers=auth).json()
    assert body["up"]["exit_code"] == 1
    assert body["remote"] is None
    assert commands(runs) == [["/usr/local/bin/d", "up"]]


def test_remote_control_that_fails_to_start_is_reported(
    client, auth, fake, monkeypatch, runs
):
    def run(command, **kwargs):
        runs.append({"command": command, **kwargs})
        if command[0].endswith("systemd-run"):
            return SimpleNamespace(returncode=1, stdout="", stderr="unit exists\n")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(projects_module.subprocess, "run", run)
    body = client.post("/projects/blog/restart", headers=auth).json()
    assert body["remote"] == {
        "unit": "uplink-remote-blog",
        "status": "failed",
        "detail": "unit exists\n",
    }


def test_remote_control_that_hangs_is_reported(client, auth, fake, monkeypatch, runs):
    def run(command, **kwargs):
        runs.append({"command": command, **kwargs})
        if command[0].startswith("/usr/local/bin/systemd") or command[0].endswith(
            "systemctl"
        ):
            raise projects_module.subprocess.TimeoutExpired(command, 30)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(projects_module.subprocess, "run", run)
    body = client.post("/projects/blog/ensure-running", headers=auth).json()
    assert body["remote"] == {
        "unit": "uplink-remote-blog",
        "status": "failed",
        "detail": "systemd-run timed out",
    }


@pytest.mark.parametrize("action", ["restart", "ensure-running"])
def test_new_actions_refuse_protected_projects(client, auth, fake, runs, action):
    response = client.post(f"/projects/Judy/{action}", headers=auth)
    assert response.status_code == 403
    assert response.json()["error"] == "protected"
    assert runs == []


@pytest.mark.parametrize("action", ["restart", "ensure-running"])
def test_new_actions_need_systemd(client, auth, fake, monkeypatch, runs, action):
    monkeypatch.setattr(
        projects_module.shutil,
        "which",
        lambda name: None if name == "systemd-run" else f"/usr/local/bin/{name}",
    )
    response = client.post(f"/projects/blog/{action}", headers=auth)
    assert response.status_code == 503
    assert response.json()["error"] == "systemd_unavailable"
    assert runs == []


@pytest.mark.parametrize("action", ["restart", "ensure-running"])
def test_new_actions_wait_their_turn(client, auth, fake, runs, app, action):
    lock = app.state.projects._locks["blog"]
    lock.acquire()
    try:
        response = client.post(f"/projects/blog/{action}", headers=auth)
    finally:
        lock.release()
    assert response.status_code == 409
    assert response.json()["error"] == "busy"
    assert runs == []


def test_new_actions_are_audited(client, auth, fake, runs, config):
    client.post("/projects/blog/restart", headers=auth)
    client.post("/projects/shop/ensure-running", headers=auth)
    restart, ensure = [
        json.loads(line) for line in config.audit_log.read_text().splitlines()[-2:]
    ]
    assert (restart["action"], restart["exit_code"], restart["remote"]) == (
        "restart",
        0,
        "started",
    )
    assert (ensure["action"], ensure["started"], ensure["remote"]) == (
        "ensure-running",
        True,
        "started",
    )


@pytest.mark.parametrize(
    "name", [".devcontainer", "-rf", "a b", "a;b", "$(id)", ".hidden"]
)
def test_refuses_invalid_names(client, auth, fake, runs, name):
    response = client.post(f"/projects/{name}/up", headers=auth)
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_name"
    assert runs == []


@pytest.mark.parametrize(
    "path",
    [
        "/projects/../up",
        "/projects/%2e%2e/up",
        "/projects/..%2Fetc/up",
        "/projects/%2Fetc/up",
    ],
)
def test_refuses_traversal(client, auth, fake, runs, path):
    response = client.post(path, headers=auth)
    assert response.status_code in {400, 404, 405}
    assert runs == []


def test_unit_refuses_dotdot_and_absolute(app, fake):
    for name in ["..", ".", "/etc", "../blog", "blog/../shop"]:
        with pytest.raises(UplinkError) as e:
            app.state.projects.resolve(name)
        assert e.value.code == "invalid_name"


def test_refuses_symlink_out_of_projects(
    client, auth, fake, runs, root, config, tmp_path
):
    outside = make_project(config, "outside", root=tmp_path)
    (root / "escape").symlink_to(outside)
    response = client.post("/projects/escape/up", headers=auth)
    assert response.status_code == 403
    assert response.json()["error"] == "outside_projects"
    assert runs == []


def test_refuses_symlink_to_another_project(client, auth, fake, runs, root):
    (root / "alias").symlink_to(root / "blog")
    assert (
        client.post("/projects/alias/up", headers=auth).json()["error"]
        == "outside_projects"
    )


def test_refuses_missing_compose_file(client, auth, fake, runs):
    response = client.post("/projects/notes/up", headers=auth)
    assert response.status_code == 404
    assert response.json()["error"] == "project_not_found"
    assert runs == []


def test_refuses_folder_whose_env_names_another(client, auth, fake, runs, config, root):
    (root / "twin" / "main").mkdir(parents=True)
    make_env(config, "twin", project_root=root / "blog")
    response = client.post("/projects/twin/up", headers=auth)
    assert response.status_code == 404
    assert response.json()["error"] == "project_not_found"
    assert runs == []


def test_refuses_folder_without_env(client, auth, fake, runs, root):
    (root / "bare" / "main").mkdir(parents=True)
    assert (
        client.post("/projects/bare/up", headers=auth).json()["error"]
        == "project_not_found"
    )


def test_protection_ignores_case(client, auth, fake, runs, config, root):
    (root / "Judy").rename(root / "judy")
    make_env(config, "judy", project_root=root / "judy")
    response = client.post("/projects/judy/up", headers=auth)
    assert response.status_code == 403
    assert response.json()["error"] == "protected"
    assert runs == []


def test_slugs_match_devenv():
    assert projects_module.slugify("Judy") == "judy"
    assert projects_module.slugify("TokyoBurnNvim") == "tokyoburnnvim"
    assert projects_module.slugify("my.repo") == "my-repo"
    assert projects_module.slugify("a..b__c") == "a-b__c"


def test_unknown_project(client, auth, fake, runs):
    assert (
        client.post("/projects/ghost/up", headers=auth).json()["error"]
        == "project_not_found"
    )


def fork(client, auth, name="newthing", url="https://github.com/someone/newthing"):
    return client.post("/projects/fork", headers=auth, json={"name": name, "url": url})


def test_fork_needs_token(client, fake):
    assert (
        client.post(
            "/projects/fork", json={"name": "x", "url": "https://github.com/a/b"}
        ).status_code
        == 401
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/someone/newthing",
        "https://github.com/some-one/new.thing.git",
        "https://github.com/someone/new_thing/",
    ],
)
def test_fork_accepts_valid_request_but_is_not_built_yet(client, auth, fake, root, url):
    response = fork(client, auth, url=url)
    assert response.status_code == 501
    assert response.json()["error"] == "not_implemented"
    assert not (root / "newthing").exists()


@pytest.mark.parametrize("name", ["..", ".hidden", "-rf", "a/b", "a b", "$(id)", ""])
def test_fork_refuses_invalid_names(client, auth, fake, name):
    response = fork(client, auth, name=name)
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_name"


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/someone/newthing",
        "git@github.com:someone/newthing.git",
        "https://gitlab.com/someone/newthing",
        "https://github.com.evil.com/someone/newthing",
        "https://github.com/someone",
        "https://github.com/someone/newthing/tree/main",
        "https://github.com/someone/newthing?x=1",
        "https://github.com/someone/new thing",
        "https://github.com/someone/..",
        "https://github.com/someone/.git",
        "file:///etc/passwd",
        "--upload-pack=touch /tmp/x",
    ],
)
def test_fork_refuses_invalid_urls(client, auth, fake, url):
    response = fork(client, auth, url=url)
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_url"


@pytest.mark.parametrize("name", ["blog", "notes", "Judy", "README.md"])
def test_fork_refuses_existing_paths(client, auth, fake, name):
    response = fork(client, auth, name=name)
    assert response.status_code == 409
    assert response.json()["error"] == "already_exists"


def test_fork_refuses_existing_symlink(client, auth, fake, root, tmp_path):
    (root / "dangling").symlink_to(tmp_path / "nowhere")
    assert fork(client, auth, name="dangling").json()["error"] == "already_exists"


@pytest.mark.parametrize(
    "body",
    [{}, {"name": "x"}, {"name": "x", "url": "https://github.com/a/b", "extra": 1}],
)
def test_fork_refuses_bad_bodies(client, auth, fake, body):
    response = client.post("/projects/fork", headers=auth, json=body)
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_request"


def test_fork_is_audited(client, auth, fake, config):
    fork(client, auth)
    record = json.loads(config.audit_log.read_text().splitlines()[-1])
    assert (record["action"], record["project"], record["outcome"]) == (
        "fork",
        "newthing",
        501,
    )
    assert record["url"] == "https://github.com/someone/newthing"
