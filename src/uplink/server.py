import os
import socket
from pathlib import Path

import uvicorn
from fastapi import FastAPI

SOCKET_MODE = 0o660


def bind_unix_socket(path: Path) -> socket.socket:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    # The umask keeps the socket from ever being world-accessible, even before the chmod.
    old_umask = os.umask(0o117)
    try:
        sock.bind(str(path))
    finally:
        os.umask(old_umask)
    os.chmod(path, SOCKET_MODE)
    return sock


def serve(app: FastAPI, path: Path) -> None:
    sock = bind_unix_socket(path)
    # uvicorn would chmod a socket it bound itself to 0666, so it gets ours instead.
    server = uvicorn.Server(uvicorn.Config(app, log_config=None, access_log=False))
    server.run(sockets=[sock])
