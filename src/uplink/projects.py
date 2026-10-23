import re

PROJECT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def valid_project_name(name: str) -> bool:
    return PROJECT_NAME.fullmatch(name) is not None and name not in {".", ".."}
