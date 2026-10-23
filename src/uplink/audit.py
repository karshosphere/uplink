import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, Request


class AuditLog:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        self._file = os.fdopen(fd, "a", buffering=1)

    def write(self, record: dict) -> None:
        line = json.dumps(record, default=str)
        self._file.write(line + "\n")
        print(line, file=sys.stdout, flush=True)


def note(request: Request, **fields) -> None:
    request.state.audit = getattr(request.state, "audit", {}) | fields


def install(app: FastAPI, log: AuditLog) -> None:
    @app.middleware("http")
    async def audit(request: Request, call_next):
        started = time.monotonic()
        record = {
            "time": datetime.now(UTC).isoformat(timespec="seconds"),
            "method": request.method,
            "path": request.url.path,
        }
        try:
            response = await call_next(request)
        except Exception:
            record["outcome"] = 500
            raise
        else:
            record["outcome"] = response.status_code
            return response
        finally:
            record["duration_ms"] = round((time.monotonic() - started) * 1000)
            record |= getattr(request.state, "audit", {})
            log.write(record)
