from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import docker
from docker.errors import APIError, DockerException, NotFound

from uplink.errors import UplinkError
from uplink.projects import valid_project_name

WORKING_DIR_LABEL = "com.docker.compose.project.working_dir"
CONTAINER_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
STOP_TIMEOUT_SECONDS = 10


class DockerAPI(Protocol):
    def containers(self, all: bool = False) -> list[dict]: ...
    def inspect_container(self, container: str) -> dict: ...
    def start(self, container: str) -> None: ...
    def stop(self, container: str, timeout: int) -> None: ...
    def restart(self, container: str, timeout: int) -> None: ...


def connect() -> DockerAPI:
    return docker.from_env().api


def owning_project(labels: dict | None, projects_dir: Path) -> str | None:
    working_dir = (labels or {}).get(WORKING_DIR_LABEL)
    if not working_dir:
        return None
    try:
        relative = Path(working_dir).resolve().relative_to(projects_dir.resolve())
    except ValueError:
        return None
    if not relative.parts or not valid_project_name(relative.parts[0]):
        return None
    return relative.parts[0]


class Containers:
    def __init__(self, connect: Callable[[], DockerAPI], projects_dir: Path, protected: tuple[str, ...]):
        self._connect = connect
        self._api: DockerAPI | None = None
        self.projects_dir = projects_dir
        self.protected = protected

    def _call(self, fn: Callable[[DockerAPI], object]):
        try:
            if self._api is None:
                self._api = self._connect()
            return fn(self._api)
        except NotFound as e:
            raise UplinkError(404, "container_not_found", e.explanation or str(e)) from None
        except APIError as e:
            raise UplinkError(502, "docker_error", e.explanation or str(e)) from None
        except DockerException as e:
            raise UplinkError(503, "docker_unavailable", str(e)) from None

    def _summary(self, entry: dict) -> dict:
        name = entry["Names"][0].lstrip("/") if entry.get("Names") else entry["Id"][:12]
        project = owning_project(entry.get("Labels"), self.projects_dir)
        return {
            "name": name,
            "image": entry.get("Image"),
            "state": entry.get("State"),
            "status": entry.get("Status"),
            "project": project,
            "controllable": project is not None and name not in self.protected,
        }

    def list(self) -> list[dict]:
        entries = self._call(lambda api: api.containers(all=True))
        return sorted((self._summary(e) for e in entries), key=lambda c: c["name"])

    def _inspect(self, name: str) -> dict:
        if not CONTAINER_NAME.fullmatch(name):
            raise UplinkError(400, "invalid_name", f"not a container name: {name!r}")
        return self._call(lambda api: api.inspect_container(name))

    def get(self, name: str) -> dict:
        attrs = self._inspect(name)
        state = attrs.get("State") or {}
        config = attrs.get("Config") or {}
        real_name = attrs["Name"].lstrip("/")
        project = owning_project(config.get("Labels"), self.projects_dir)
        return {
            "name": real_name,
            "id": attrs["Id"][:12],
            "image": config.get("Image"),
            "state": state.get("Status"),
            "health": (state.get("Health") or {}).get("Status"),
            "started_at": state.get("StartedAt"),
            "finished_at": state.get("FinishedAt"),
            "exit_code": state.get("ExitCode"),
            "restart_count": attrs.get("RestartCount"),
            "project": project,
            "controllable": project is not None and real_name not in self.protected,
        }

    def _controllable(self, name: str) -> dict:
        details = self.get(name)
        # The check uses the inspected name, so an ID or prefix cannot slip past the protected list.
        if details["name"] in self.protected:
            raise UplinkError(403, "protected", f"{details['name']} is protected")
        if details["project"] is None:
            raise UplinkError(403, "not_a_project", f"{details['name']} does not belong to a project in {self.projects_dir}")
        return details

    def start(self, name: str) -> dict:
        target = self._controllable(name)["name"]
        self._call(lambda api: api.start(target))
        return self.get(target)

    def stop(self, name: str) -> dict:
        target = self._controllable(name)["name"]
        self._call(lambda api: api.stop(target, timeout=STOP_TIMEOUT_SECONDS))
        return self.get(target)

    def restart(self, name: str) -> dict:
        target = self._controllable(name)["name"]
        self._call(lambda api: api.restart(target, timeout=STOP_TIMEOUT_SECONDS))
        return self.get(target)
