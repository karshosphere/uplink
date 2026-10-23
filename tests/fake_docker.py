from docker.errors import APIError, NotFound

PROJECT_LABEL = "com.docker.compose.project"
WORKING_DIR_LABEL = "com.docker.compose.project.working_dir"


class FakeDocker:
    def __init__(self):
        self.by_name: dict[str, dict] = {}
        self.calls: list[tuple[str, str]] = []
        self.fail_with: APIError | None = None

    def add(self, name: str, state: str = "exited", working_dir: str | None = None, image: str = "img:latest"):
        labels = {}
        if working_dir:
            labels = {PROJECT_LABEL: name.split("-")[0], WORKING_DIR_LABEL: working_dir}
        self.by_name[name] = {
            "Id": f"{len(self.by_name):02d}" + "ab" * 31,
            "Name": f"/{name}",
            "Image": image,
            "State": state,
            "Labels": labels,
        }

    def _find(self, ref: str) -> dict:
        for c in self.by_name.values():
            if c["Name"] == f"/{ref}" or c["Id"].startswith(ref):
                return c
        raise NotFound(f"No such container: {ref}")

    def containers(self, all=False):
        return [
            {"Id": c["Id"], "Names": [c["Name"]], "Image": c["Image"], "State": c["State"],
             "Status": "Up 1 minute" if c["State"] == "running" else "Exited (0)", "Labels": c["Labels"]}
            for c in self.by_name.values()
        ]

    def inspect_container(self, ref):
        c = self._find(ref)
        return {
            "Id": c["Id"],
            "Name": c["Name"],
            "RestartCount": 0,
            "State": {"Status": c["State"], "StartedAt": "2026-10-15T00:00:00Z",
                      "FinishedAt": "0001-01-01T00:00:00Z", "ExitCode": 0},
            "Config": {"Image": c["Image"], "Labels": c["Labels"]},
        }

    def _act(self, action, ref, new_state):
        self.calls.append((action, ref))
        if self.fail_with:
            raise self.fail_with
        self._find(ref)["State"] = new_state

    def start(self, ref):
        self._act("start", ref, "running")

    def stop(self, ref, timeout):
        self._act("stop", ref, "exited")

    def restart(self, ref, timeout):
        self._act("restart", ref, "running")
