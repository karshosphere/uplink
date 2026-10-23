# Uplink

A small REST server that runs on the host so the `judy` container can act on
it without the Docker socket or a shell. Every action is a fixed, named route.
There is no generic "run this command" endpoint.

## How it is reached

Uplink listens on a Unix socket, not TCP:

```
~/.local/state/uplink/uplink.sock   (mode 0660)
```

Every route except `/health` needs `Authorization: Bearer <token>`. The token is
in `~/.config/uplink/token`, and `install.sh` writes a copy to
`~/.config/uplink/judy.env` as `UPLINK_TOKEN` for the container's env file.

```sh
curl --unix-socket ~/.local/state/uplink/uplink.sock \
     -H "Authorization: Bearer $UPLINK_TOKEN" \
     http://uplink/health
```

Errors always come back as `{"error": "<code>", "detail": "<text>"}`.

## Routes

| Method | Path      | Returns                                        |
| ------ | --------- | ---------------------------------------------- |
| GET    | `/health` | `{"ok": true, "version": "…"}`, no auth needed |

### System (read only)

| Method | Path             | Returns                                               |
| ------ | ---------------- | ----------------------------------------------------- |
| GET    | `/system/stats`  | everything below in one call                          |
| GET    | `/system/temps`  | sensor readings per chip: label, current, high, critical |
| GET    | `/system/memory` | RAM and swap: total, used, available or free, percent |
| GET    | `/system/cpu`    | core count, overall and per-core usage, load averages |
| GET    | `/system/disk`   | usage for each mount in `disk_mounts`                 |
| GET    | `/system/uptime` | boot time and uptime in seconds                       |

Sizes are in bytes and temperatures in degrees Celsius. CPU usage is sampled
over 0.2 seconds, so `/system/cpu` and `/system/stats` take that long to answer.

### Containers

| Method | Path                         | Does                                              |
| ------ | ---------------------------- | ------------------------------------------------- |
| GET    | `/containers`                | list all: name, image, state, status, project, controllable |
| GET    | `/containers/{name}`         | one container's state, health, timings and project |
| POST   | `/containers/{name}/start`   | start a stopped container                         |
| POST   | `/containers/{name}/stop`    | stop a running container (10 second grace period) |
| POST   | `/containers/{name}/restart` | restart                                           |

Listing covers every container on the host. Start, stop and restart only touch
a container whose `com.docker.compose.project.working_dir` label resolves to a
folder directly inside `projects_dir`; anything else gets `403 not_a_project`.
Containers named in `protected` (by default `judy`) get `403 protected`, so
Judy cannot stop itself. Both checks use the name Docker reports, so
addressing a container by ID does not get round them.

### Projects

| Method | Path                  | Does                                                  |
| ------ | --------------------- | ----------------------------------------------------- |
| GET    | `/projects`           | folders in `projects_dir` with a compose file, and their containers |
| POST   | `/projects/{name}/up` | `d up` in the project folder                          |
| POST   | `/projects/{name}/down` | `d down` in the project folder                      |

A project name must match `^[A-Za-z0-9][A-Za-z0-9._-]*$`, must be a real folder
directly inside `projects_dir` (not a symlink), and must contain
`.devcontainer/compose.yaml`. Anything else is refused before `d` runs.

`up` and `down` run synchronously, so `d up` can take minutes while it builds.
The response carries the command, its exit code and the last 64 KiB of stdout
and stderr. A run longer than `command_timeout_seconds` is killed and comes back
with `"timed_out": true`. Only one action runs per project at a time; a second
gets `409 busy`.

## Install

On the host, as your own user (never root), with [uv](https://docs.astral.sh/uv/)
installed and your user in the `docker` group:

```sh
deploy/install.sh
```

It creates the config and state directories, generates a token if there is none,
installs Uplink into `~/.local/bin` and enables the `uplink` systemd user
service. Re-run it after pulling changes. To keep the service running while you
are logged out:

```sh
loginctl enable-linger "$USER"
```

## Config

`~/.config/uplink/config.toml`, or the file named in `UPLINK_CONFIG`. Every key
is optional; unknown keys stop the service from starting.

```toml
projects_dir = "~/Projects"
socket_path  = "~/.local/state/uplink/uplink.sock"
token_file   = "~/.config/uplink/token"
audit_log    = "~/.local/state/uplink/audit.log"
protected    = ["judy"]
disk_mounts  = ["/", "/home"]
command_timeout_seconds = 900
d_command    = "d"
```

`d_command` is looked up on the service's `PATH`, which under systemd is short.
If `d` lives somewhere like `~/bin`, give its full path.

## Audit log

Every request is appended to `~/.local/state/uplink/audit.log` as one JSON
line with its time, method, path, status and duration. The same lines go to
stdout, so `journalctl --user -u uplink` has them too.

## Smoke test

After installing on the host:

```sh
sock=~/.local/state/uplink/uplink.sock
token=$(cat ~/.config/uplink/token)

systemctl --user status uplink
stat -c '%a' "$sock"                                    # 660
curl -s --unix-socket "$sock" http://uplink/health      # {"ok":true,...}
curl -s --unix-socket "$sock" http://uplink/nowhere \
     -H "Authorization: Bearer $token"                  # {"error":"not_found",...}
curl -s --unix-socket "$sock" http://uplink/system/stats # {"error":"unauthorised",...}
curl -s --unix-socket "$sock" http://uplink/system/stats \
     -H "Authorization: Bearer $token"                  # temps, memory, cpu, disk, uptime
curl -s --unix-socket "$sock" http://uplink/containers \
     -H "Authorization: Bearer $token"                  # every container on the host
curl -s --unix-socket "$sock" -X POST http://uplink/containers/judy/stop \
     -H "Authorization: Bearer $token"                  # {"error":"protected",...}
curl -s --unix-socket "$sock" http://uplink/projects \
     -H "Authorization: Bearer $token"                  # every project with a compose file
curl -s --unix-socket "$sock" -X POST http://uplink/projects/uplink/up \
     -H "Authorization: Bearer $token"                  # exit code and output of d up
tail -n 2 ~/.local/state/uplink/audit.log
```

## Development

```sh
uv sync
uv run pytest
```

The tests use fakes in place of Docker, psutil and subprocess, so they run in a
container with no Docker socket.
