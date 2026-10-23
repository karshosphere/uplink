from pathlib import Path

import pytest

from uplink.config import Config, ConfigError, load_config


def write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(body)
    return path


def test_missing_file_gives_defaults(tmp_path):
    assert load_config(tmp_path / "nope.toml") == Config()


def test_defaults_live_under_home():
    config = Config()
    assert config.projects_dir == Path.home() / "Projects"
    assert config.protected == ("judy",)
    assert config.command_timeout_seconds == 900


def test_reads_every_key(tmp_path):
    path = write(tmp_path, """
projects_dir = "/srv/projects"
socket_path = "/run/uplink.sock"
token_file = "/etc/uplink/token"
audit_log = "/var/log/uplink.log"
protected = ["judy", "db"]
disk_mounts = ["/"]
command_timeout_seconds = 60
""")
    config = load_config(path)
    assert config.projects_dir == Path("/srv/projects")
    assert config.socket_path == Path("/run/uplink.sock")
    assert config.token_file == Path("/etc/uplink/token")
    assert config.audit_log == Path("/var/log/uplink.log")
    assert config.protected == ("judy", "db")
    assert config.disk_mounts == ("/",)
    assert config.command_timeout_seconds == 60


def test_expands_tilde(tmp_path):
    config = load_config(write(tmp_path, 'projects_dir = "~/elsewhere"'))
    assert config.projects_dir == Path.home() / "elsewhere"


def test_env_var_picks_the_file(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLINK_CONFIG", str(write(tmp_path, "command_timeout_seconds = 5")))
    assert load_config().command_timeout_seconds == 5


@pytest.mark.parametrize("body", [
    'colour = "blue"',
    "projects_dir = 3",
    'protected = "judy"',
    "protected = [1]",
    "command_timeout_seconds = 0",
    "command_timeout_seconds = true",
    "not toml at all",
])
def test_rejects_bad_config(tmp_path, body):
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, body))
