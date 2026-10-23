from fastapi import APIRouter, Request

from uplink import audit

router = APIRouter(prefix="/containers")


def containers(request: Request):
    return request.app.state.containers


@router.get("")
def list_containers(request: Request):
    return {"containers": containers(request).list()}


@router.get("/{name}")
def get_container(request: Request, name: str):
    return containers(request).get(name)


def _act(request: Request, name: str, action: str) -> dict:
    audit.note(request, action=action, container=name)
    result = getattr(containers(request), action)(name)
    audit.note(request, state=result["state"])
    return result


@router.post("/{name}/start")
def start(request: Request, name: str):
    return _act(request, name, "start")


@router.post("/{name}/stop")
def stop(request: Request, name: str):
    return _act(request, name, "stop")


@router.post("/{name}/restart")
def restart(request: Request, name: str):
    return _act(request, name, "restart")
