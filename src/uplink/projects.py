from __future__ import annotations

import re
import shutil
import subprocess
import threading
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

from uplink.config import Config
from uplink.errors import UplinkError

if TYPE_CHECKING:
    from uplink.docker_ops import Containers

PROJECT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
COMPOSE_FILE = Path(".devcontainer/compose.yaml")
OUTPUT_LIMIT = 64 * 1024


def valid_project_name(name: str) -> bool:
    return PROJECT_NAME.fullmatch(name) is not None and name not in {".", ".."}


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
            raise UplinkError(403, "outside_projects", f"{name} resolves outside {root}")
        if not (path / COMPOSE_FILE).is_file():
            raise UplinkError(404, "project_not_found", f"no {COMPOSE_FILE} in {path}")
        return path

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
                    {"name": container["name"], "state": container["state"], "status": container["status"]}
                )
        return [
            {"name": name, "path": str(self.resolve(name)), "containers": by_project.get(name, [])}
            for name in self._names()
        ]

    def run_d(self, name: str, action: str) -> dict:
        path = self.resolve(name)
        d = shutil.which(self.config.d_command)
        if d is None:
            raise UplinkError(503, "d_unavailable", f"{self.config.d_command} is not on the PATH")
        command = [d, action]
        lock = self._locks[name]
        if not lock.acquire(blocking=False):
            raise UplinkError(409, "busy", f"{name} already has an action running")
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
                "command": command, "exit_code": None, "timed_out": True,
                "stdout": _tail(_text(e.stdout)), "stderr": _tail(_text(e.stderr)),
            }
        finally:
            lock.release()
        return {
            "command": command, "exit_code": done.returncode, "timed_out": False,
            "stdout": _tail(done.stdout), "stderr": _tail(done.stderr),
        }
