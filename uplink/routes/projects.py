from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

from uplink import audit

router = APIRouter(prefix="/projects")


def projects(request: Request):
    return request.app.state.projects


@router.get("")
def list_projects(request: Request):
    return {"projects": projects(request).list()}


class ForkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    url: str


@router.post("/fork")
def fork(request: Request, body: ForkRequest):
    audit.note(request, project=body.name, action="fork", url=body.url)
    return projects(request).fork(body.name, body.url)


def _run_d(request: Request, name: str, action: str) -> dict:
    audit.note(request, project=name, action=action)
    result = projects(request).run_d(name, action)
    audit.note(
        request,
        command=result["command"],
        exit_code=result["exit_code"],
        timed_out=result["timed_out"],
    )
    return result


@router.post("/{name}/up")
def up(request: Request, name: str):
    return _run_d(request, name, "up")


@router.post("/{name}/down")
def down(request: Request, name: str):
    return _run_d(request, name, "down")


@router.post("/{name}/restart")
def restart(request: Request, name: str):
    audit.note(request, project=name, action="restart")
    result = projects(request).restart(name)
    audit.note(
        request,
        command=result["command"],
        exit_code=result["exit_code"],
        timed_out=result["timed_out"],
        remote=(result["remote"] or {}).get("status"),
    )
    return result


@router.post("/{name}/ensure-running")
def ensure_running(request: Request, name: str):
    audit.note(request, project=name, action="ensure-running")
    result = projects(request).ensure_running(name)
    audit.note(
        request,
        started=result["started"],
        exit_code=(result["up"] or {}).get("exit_code"),
        remote=(result["remote"] or {}).get("status"),
    )
    return result
