from fastapi import APIRouter

from uplink import __version__

public = APIRouter()


@public.get("/health")
def health():
    return {"ok": True, "version": __version__}
