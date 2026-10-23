import sys

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from uplink import audit
from uplink.auth import TokenError, read_token, require_token
from uplink.config import Config, ConfigError, load_config
from uplink.errors import UplinkError
from uplink.routes import system
from uplink.server import serve

_HTTP_CODES = {404: "not_found", 405: "method_not_allowed"}


def error_response(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse({"error": code, "detail": detail}, status_code=status)


def add_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(UplinkError)
    async def uplink_error(request: Request, exc: UplinkError):
        return error_response(exc.status, exc.code, exc.detail)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return error_response(exc.status_code, _HTTP_CODES.get(exc.status_code, "http_error"), str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return error_response(422, "invalid_request", str(exc.errors()))


def create_app(config: Config, token: str) -> FastAPI:
    app = FastAPI(title="uplink", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.config = config
    app.state.token = token
    add_error_handlers(app)
    audit.install(app, audit.AuditLog(config.audit_log))
    app.include_router(system.public)
    app.include_router(authed_routes(), dependencies=[Depends(require_token)])
    return app


def authed_routes() -> APIRouter:
    router = APIRouter()
    return router


def main() -> None:
    try:
        config = load_config()
        token = read_token(config.token_file)
    except (ConfigError, TokenError) as e:
        sys.exit(f"uplink: {e}")
    serve(create_app(config, token), config.socket_path)
