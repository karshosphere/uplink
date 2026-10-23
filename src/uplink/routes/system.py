from fastapi import APIRouter, Request

from uplink import __version__, stats

public = APIRouter()
router = APIRouter(prefix="/system")


@public.get("/health")
def health():
    return {"ok": True, "version": __version__}


@router.get("/stats")
def all_stats(request: Request):
    return stats.everything(request.app.state.config.disk_mounts)


@router.get("/temps")
def temps():
    return stats.temps()


@router.get("/memory")
def memory():
    return stats.memory()


@router.get("/cpu")
def cpu():
    return stats.cpu()


@router.get("/disk")
def disk(request: Request):
    return stats.disk(request.app.state.config.disk_mounts)


@router.get("/uptime")
def uptime():
    return stats.uptime()
