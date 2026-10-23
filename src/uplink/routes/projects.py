from fastapi import APIRouter, Request

from uplink import audit

router = APIRouter(prefix="/projects")


def projects(request: Request):
    return request.app.state.projects


@router.get("")
def list_projects(request: Request):
    return {"projects": projects(request).list()}


def _run_d(request: Request, name: str, action: str) -> dict:
    audit.note(request, project=name, action=action)
    result = projects(request).run_d(name, action)
    audit.note(request, command=result["command"], exit_code=result["exit_code"], timed_out=result["timed_out"])
    return result


@router.post("/{name}/up")
def up(request: Request, name: str):
    return _run_d(request, name, "up")


@router.post("/{name}/down")
def down(request: Request, name: str):
    return _run_d(request, name, "down")
