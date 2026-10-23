import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_ENV = "UPLINK_CONFIG"


def default_config_path() -> Path:
    return Path.home() / ".config/uplink/config.toml"


def _home(relative: str) -> Path:
    return Path.home() / relative


@dataclass(frozen=True)
class Config:
    projects_dir: Path = field(default_factory=lambda: _home("Projects"))
    socket_path: Path = field(default_factory=lambda: _home(".local/state/uplink/uplink.sock"))
    token_file: Path = field(default_factory=lambda: _home(".config/uplink/token"))
    audit_log: Path = field(default_factory=lambda: _home(".local/state/uplink/audit.log"))
    protected: tuple[str, ...] = ("judy",)
    protected_projects: tuple[str, ...] = ("Judy",)
    disk_mounts: tuple[str, ...] = ("/", "/home")
    command_timeout_seconds: int = 900
    d_command: str = "d"


class ConfigError(Exception):
    pass


_PATH_KEYS = {"projects_dir", "socket_path", "token_file", "audit_log"}
_LIST_KEYS = {"protected", "protected_projects", "disk_mounts"}


def _coerce(key: str, value):
    if key in _PATH_KEYS:
        if not isinstance(value, str):
            raise ConfigError(f"{key} must be a string")
        return Path(value).expanduser()
    if key in _LIST_KEYS:
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ConfigError(f"{key} must be a list of strings")
        return tuple(value)
    if key == "d_command":
        if not isinstance(value, str) or not value:
            raise ConfigError(f"{key} must be a non-empty string")
        return value
    if key == "command_timeout_seconds":
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ConfigError(f"{key} must be a positive integer")
        return value
    raise ConfigError(f"unknown key: {key}")


def load_config(path: Path | None = None) -> Config:
    if path is None:
        path = Path(os.environ.get(CONFIG_ENV) or default_config_path())
    if not path.exists():
        return Config()
    with path.open("rb") as f:
        try:
            raw = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"{path}: {e}") from e
    return Config(**{key: _coerce(key, value) for key, value in raw.items()})
