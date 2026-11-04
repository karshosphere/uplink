from __future__ import annotations

import re
import shutil
import subprocess
import threading
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from uplink.config import Config
from uplink.errors import UplinkError

if TYPE_CHECKING:
    from uplink.docker_ops import Containers

PROJECT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
COMPOSE_FILE = "compose.yaml"
OUTPUT_LIMIT = 64 * 1024
SYSTEMD_TIMEOUT_SECONDS = 30
REMOTE_UNIT_PREFIX = "uplink-remote-"
GITHUB_REPO_URL = re.compile(
    r"https://github\.com/([A-Za-z0-9-]+)/([A-Za-z0-9][A-Za-z0-9._-]*?)(?:\.git)?/?"
)


def valid_project_name(name: str) -> bool:
    return PROJECT_NAME.fullmatch(name) is not None and name not in {".", ".."}


def slugify(name: str) -> str:
    # matches devenv's slugify, which names each project's folder in envs_dir
    return re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-")


def _env_project_root(env_dir: Path) -> Path | None:
    try:
        lines = (env_dir / ".env").read_text().splitlines()
    except OSError:
        return None
    for line in lines:
        key, sep, value = line.partition("=")
        if sep and key == "PROJECT_ROOT":
            return Path(value)
    return None


def project_for_env(env_dir: Path, config: Config) -> str | None:
    """The project a devenv folder belongs to, when its .env names exactly one."""
    if not (env_dir / COMPOSE_FILE).is_file():
        return None
    root = _env_project_root(env_dir)
    if root is None or root.is_symlink():
        return None
    root = root.resolve()
    if root.parent != config.projects_dir.resolve() or not root.is_dir():
        return None
    if not valid_project_name(root.name) or slugify(root.name) != env_dir.name:
        return None
    return root.name


def is_protected_project(name: str, config: Config) -> bool:
    return slugify(name) in {slugify(p) for p in config.protected_projects}


def _tail(text: str) -> str:
    return text[-OUTPUT_LIMIT:]


def _text(output: str | bytes | None) -> str:
    if isinstance(output, bytes):
        return output.decode(errors="replace")
    return output or ""


class Projects:
    def __init__(self, config: Config, containers: Containers):
        self.config = config
        self.containers = containers
        self._locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)

    def resolve(self, name: str) -> Path:
        if not valid_project_name(name):
            raise UplinkError(400, "invalid_name", f"not a project name: {name!r}")
        root = self.config.projects_dir.resolve()
        if (root / name).is_symlink():
            raise UplinkError(403, "outside_projects", f"{name} is a symlink")
        path = (root / name).resolve()
        if path.parent != root:
            raise UplinkError(
                403, "outside_projects", f"{name} resolves outside {root}"
            )
        env_dir = self.config.envs_dir.resolve() / slugify(name)
        if project_for_env(env_dir, self.config) != name:
            raise UplinkError(
                404,
                "project_not_found",
                f"{env_dir} holds no devenv project for {path}",
            )
        return path

    def _actionable(self, name: str) -> Path:
        path = self.resolve(name)
        if is_protected_project(name, self.config):
            raise UplinkError(403, "protected", f"{name} is protected")
        return path

    def fork(self, name: str, url: str) -> dict:
        if not valid_project_name(name):
            raise UplinkError(400, "invalid_name", f"not a project name: {name!r}")
        if not GITHUB_REPO_URL.fullmatch(url):
            raise UplinkError(
                400, "invalid_url", f"not a GitHub repository URL: {url!r}"
            )
        target = self.config.projects_dir / name
        if target.exists() or target.is_symlink():
            raise UplinkError(409, "already_exists", f"{target} already exists")
        raise UplinkError(501, "not_implemented", "forking is not built yet")

    def _names(self) -> list[str]:
        root = self.config.projects_dir
        if not root.is_dir():
            return []
        found = []
        for entry in sorted(root.iterdir()):
            try:
                self.resolve(entry.name)
            except UplinkError:
                continue
            found.append(entry.name)
        return found

    def list(self) -> list[dict]:
        by_project = defaultdict(list)
        for container in self.containers.list():
            if container["project"]:
                by_project[container["project"]].append(
                    {
                        "name": container["name"],
                        "state": container["state"],
                        "status": container["status"],
                    }
                )
        return [
            {
                "name": name,
                "path": str(self.resolve(name)),
                "protected": is_protected_project(name, self.config),
                "containers": by_project.get(name, []),
            }
            for name in self._names()
        ]

    def _d(self) -> str:
        d = shutil.which(self.config.d_command)
        if d is None:
            raise UplinkError(
                503, "d_unavailable", f"{self.config.d_command} is not on the PATH"
            )
        return d

    def _systemd(self) -> tuple[str, str]:
        systemctl, systemd_run = shutil.which("systemctl"), shutil.which("systemd-run")
        if systemctl is None or systemd_run is None:
            raise UplinkError(
                503, "systemd_unavailable", "systemctl and systemd-run are needed"
            )
        return systemctl, systemd_run

    @contextmanager
    def _busy(self, name: str) -> Iterator[None]:
        lock = self._locks[name]
        if not lock.acquire(blocking=False):
            raise UplinkError(409, "busy", f"{name} already has an action running")
        try:
            yield
        finally:
            lock.release()

    def _run(self, command: list[str], path: Path) -> dict:
        try:
            done = subprocess.run(
                command,
                cwd=path,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=self.config.command_timeout_seconds,
            )
        except subprocess.TimeoutExpired as e:
            return {
                "command": command,
                "exit_code": None,
                "timed_out": True,
                "stdout": _tail(_text(e.stdout)),
                "stderr": _tail(_text(e.stderr)),
            }
        return {
            "command": command,
            "exit_code": done.returncode,
            "timed_out": False,
            "stdout": _tail(done.stdout),
            "stderr": _tail(done.stderr),
        }

    def run_d(self, name: str, action: str) -> dict:
        path = self._actionable(name)
        d = self._d()
        with self._busy(name):
            return self._run([d, action], path)

    def _systemctl(self, systemctl: str, *args: str) -> int:
        try:
            return subprocess.run(
                [systemctl, "--user", *args],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=SYSTEMD_TIMEOUT_SECONDS,
            ).returncode
        except subprocess.TimeoutExpired:
            return 124

    def _start_remote(self, unit: str, systemd_run: str, d: str, path: Path) -> dict:
        # its own unit, so the session outlives a restart of uplink and logs to the journal
        try:
            done = subprocess.run(
                [
                    systemd_run,
                    "--user",
                    f"--unit={unit}",
                    "--collect",
                    "--quiet",
                    f"--working-directory={path}",
                    "--",
                    d,
                    "cr",
                ],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=SYSTEMD_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return {"unit": unit, "status": "failed", "detail": "systemd-run timed out"}
        if done.returncode != 0:
            return {"unit": unit, "status": "failed", "detail": _tail(done.stderr)}
        return {"unit": unit, "status": "started"}

    def restart(self, name: str) -> dict:
        path = self._actionable(name)
        d = self._d()
        systemctl, systemd_run = self._systemd()
        unit = REMOTE_UNIT_PREFIX + slugify(name)
        with self._busy(name):
            self._systemctl(systemctl, "stop", unit)
            result = self._run([d, "restart"], path)
            result["remote"] = (
                self._start_remote(unit, systemd_run, d, path)
                if result["exit_code"] == 0
                else None
            )
        return result

    def ensure_running(self, name: str) -> dict:
        path = self._actionable(name)
        d = self._d()
        systemctl, systemd_run = self._systemd()
        unit = REMOTE_UNIT_PREFIX + slugify(name)
        with self._busy(name):
            running = any(
                c["project"] == name and c["state"] == "running"
                for c in self.containers.list()
            )
            result = {"started": not running, "up": None, "remote": None}
            if not running:
                result["up"] = self._run([d, "up"], path)
                if result["up"]["exit_code"] != 0:
                    return result
            if self._systemctl(systemctl, "is-active", "--quiet", unit) == 0:
                result["remote"] = {"unit": unit, "status": "already_running"}
            else:
                result["remote"] = self._start_remote(unit, systemd_run, d, path)
        return result
