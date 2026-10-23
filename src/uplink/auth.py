import hmac
import stat
from pathlib import Path

from fastapi import Request

from uplink.errors import UplinkError


class TokenError(Exception):
    pass


def read_token(path: Path) -> str:
    try:
        mode = path.stat().st_mode
    except FileNotFoundError:
        raise TokenError(f"{path} does not exist; run deploy/install.sh") from None
    if stat.S_IMODE(mode) & 0o077:
        raise TokenError(f"{path} must not be readable by group or others (chmod 600)")
    token = path.read_text().strip()
    if not token:
        raise TokenError(f"{path} is empty")
    return token


def require_token(request: Request) -> None:
    scheme, _, given = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not given:
        raise UplinkError(401, "unauthorised", "missing bearer token")
    if not hmac.compare_digest(given.encode(), request.app.state.token.encode()):
        raise UplinkError(401, "unauthorised", "wrong token")
