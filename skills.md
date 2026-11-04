---
name: uplink
description: Use Uplink to read the host machine's vitals (temperatures, CPU, memory, disk, uptime) and to list, start, stop and restart project containers or bring whole projects up and down. Use it whenever a task needs to know or change something on the host from inside a container.
---

# Uplink

Uplink is a small REST service on the host. It is the only way for an agent in a
container to see or act on the host machine. It has no shell and no generic
"run this command" route: every action is a fixed, named route, and anything not
listed here cannot be done through it.

Every request is written to the host's audit log, including refused ones.

## Connecting

Uplink listens on a Unix socket, not on a TCP port:

```
~/.local/state/uplink/uplink.sock
```

Every route except `/health` needs the bearer token from the `UPLINK_TOKEN`
environment variable. The host name in the URL is ignored; use `http://uplink`.

A small helper keeps the calls short:

```sh
uplink() {
    method=$1; path=$2; shift 2
    curl -sS --unix-socket ~/.local/state/uplink/uplink.sock \
         -X "$method" \
         -H "Authorization: Bearer $UPLINK_TOKEN" \
         "$@" "http://uplink$path"
}

uplink GET /health
```

Responses are JSON. Errors always have the same shape, with a non-2xx status:

```json
{"error": "protected", "detail": "judy is protected"}
```

Read `error` to decide what to do; `detail` is for people.

## Reading the host's vitals

All of these are read-only and safe to call at any time.

| Call | Returns |
| --- | --- |
| `GET /health` | `{"ok": true, "version": "..."}`; needs no token |
| `GET /system/stats` | everything below in one response, keyed `temps`, `memory`, `cpu`, `disk`, `uptime` |
| `GET /system/temps` | `{"sensors": {chip: [{"label", "current", "high", "critical"}]}}` |
| `GET /system/memory` | `{"ram": {"total", "used", "available", "percent"}, "swap": {"total", "used", "free", "percent"}}` |
| `GET /system/cpu` | `{"count", "percent", "per_core": [...], "load_average": {"1m", "5m", "15m"}}` |
| `GET /system/disk` | `{"mounts": [{"mount", "total", "used", "free", "percent"}]}` |
| `GET /system/uptime` | `{"boot_time", "uptime_seconds"}` |

- Sizes are in bytes, temperatures in degrees Celsius, percentages from 0 to 100.
- `/system/cpu` and `/system/stats` sample the CPU for 0.2 seconds before answering.
- `sensors` is empty on hosts where temperatures cannot be read; that is not an error.
- A disk mount that cannot be read comes back as `{"mount": "...", "error": "..."}`
  rather than failing the whole call.

Prefer one `GET /system/stats` over several separate calls when you need more
than one reading.

## Containers

| Call | Does |
| --- | --- |
| `GET /containers` | lists every container on the host: `name`, `image`, `state`, `status`, `project`, `controllable` |
| `GET /containers/{name}` | one container: `name`, `id`, `image`, `state`, `health`, `started_at`, `finished_at`, `exit_code`, `restart_count`, `project`, `controllable` |
| `POST /containers/{name}/start` | starts it, then returns the same fields as `GET /containers/{name}` |
| `POST /containers/{name}/stop` | stops it with a 10 second grace period, then returns its fields |
| `POST /containers/{name}/restart` | restarts it with the same grace period, then returns its fields |

You can look at every container, but you can only start, stop or restart one
whose `controllable` is `true`. That means:

- it belongs to a project folder in `~/Blackwall`, and
- it is not protected. The `judy` container, and every container in the `Judy`
  project, are protected so that Judy cannot stop itself.

Check `controllable` before acting. Addressing a container by its ID instead of
its name does not get round either rule; Uplink checks the real name.

## Projects

A project is a folder directly inside `~/Blackwall` that devenv set up. Its
compose file lives on the host, out of every container's reach.

| Call | Does |
| --- | --- |
| `GET /projects` | lists projects: `name`, `path`, `protected`, and `containers` (each with `name`, `state`, `status`) |
| `POST /projects/{name}/up` | runs `d up` in the project folder |
| `POST /projects/{name}/down` | runs `d down` in the project folder |
| `POST /projects/{name}/restart` | restarts the project's container, then starts Claude remote control in it |
| `POST /projects/{name}/ensure-running` | brings the project up if it is not running, and starts remote control unless it already runs |
| `POST /projects/fork` | not built yet; see below |

`up` and `down` answer only when the command finishes, which can take several
minutes while images build. Give the request a long timeout, longer than the
host's limit, which is 15 minutes by default:

```sh
uplink POST /projects/blog/up --max-time 960
```

The response tells you how it went:

```json
{
  "command": ["/usr/local/bin/d", "up"],
  "exit_code": 0,
  "timed_out": false,
  "stdout": "...",
  "stderr": ""
}
```

- A `200` only means Uplink ran the command. Check `exit_code`: anything other
  than `0` means `d` itself failed, and `stderr` usually says why.
- `"timed_out": true` means the run was killed at the host's limit and
  `exit_code` is `null`.
- `stdout` and `stderr` hold the last 64 KiB of output each.
- Only one action runs per project at a time. A second one gets `409 busy`.
- Project names must match `^[A-Za-z0-9][A-Za-z0-9._-]*$`. Protected projects
  are listed with `"protected": true`, and every action on them is refused.

### Restarting a project, or making sure it runs

`restart` answers like `up`, with one more field, `remote`, once the container is
back:

```json
{
  "command": ["/usr/local/bin/d", "restart"],
  "exit_code": 0,
  "timed_out": false,
  "stdout": "...",
  "stderr": "",
  "remote": {"unit": "uplink-remote-blog", "status": "started"}
}
```

`ensure-running` is the gentle one: it changes nothing that is already running.

```json
{
  "started": true,
  "up": {"command": ["/usr/local/bin/d", "up"], "exit_code": 0, "...": "..."},
  "remote": {"unit": "uplink-remote-blog", "status": "started"}
}
```

- `started` says whether the container had to be brought up; `up` holds that run,
  or is `null` when it was already running.
- `remote.status` is `started`, `already_running`, or `failed` with a `detail`.
- `remote` is `null` when the restart or the `up` failed; check `exit_code` first.
- Use `ensure-running` when you only need a project to be available. `restart`
  ends whatever its agent was doing, so use it only when that is the point.

`POST /projects/fork` takes `{"name": "<folder>", "url": "https://github.com/<owner>/<repo>"}`.
It checks the name and URL and then answers `501 not_implemented`, because the
forking itself does not exist yet. Do not rely on it. The body must be sent as
JSON:

```sh
uplink POST /projects/fork -H 'Content-Type: application/json' \
       -d '{"name": "newthing", "url": "https://github.com/someone/newthing"}'
```

## Errors

| Status | `error` | Meaning | What to do |
| --- | --- | --- | --- |
| 400 | `invalid_name` | not a valid container or project name | fix the name; do not retry as is |
| 400 | `invalid_url` | fork URL is not `https://github.com/<owner>/<repo>` | fix the URL |
| 401 | `unauthorised` | token missing or wrong | check `UPLINK_TOKEN` is set and sent |
| 403 | `protected` | the container or project is protected | stop; this is deliberate |
| 403 | `not_a_project` | the container does not belong to a project in `~/Blackwall` | stop; Uplink will never control it |
| 403 | `outside_projects` | the project folder is a symlink or resolves outside `~/Blackwall` | stop |
| 404 | `container_not_found` | no container by that name | list containers and check the name |
| 404 | `project_not_found` | no such folder, or devenv has not set it up | list projects and check the name |
| 404 | `not_found` | no such route | check the path against this guide |
| 405 | `method_not_allowed` | wrong HTTP method for the route | check GET or POST |
| 409 | `busy` | an `up` or `down` is already running on that project | wait for it to finish, then try again |
| 409 | `already_exists` | the fork target folder already exists | pick another name |
| 422 | `invalid_request` | the request body is malformed or has unknown fields | fix the body |
| 501 | `not_implemented` | the route exists but does nothing yet | do not retry |
| 502 | `docker_error` | Docker refused the action | read `detail`; retrying the same action rarely helps |
| 503 | `docker_unavailable` | Uplink cannot reach Docker | wait and try once more; report it if it persists |
| 503 | `d_unavailable` | the host's `d` script cannot be found | report it; nothing on your side will fix it |
| 503 | `systemd_unavailable` | the host has no `systemctl` or `systemd-run`, which `restart` and `ensure-running` need | report it; nothing was changed |

A `403` is an answer, not a fault. Do not look for another way to do the same
thing.

## Good habits

- Read before you write: check `GET /containers/{name}` or `GET /projects` first,
  so you act on the right thing and know its current state.
- Do not poll quickly. Every call is logged and the CPU readings cost 0.2 seconds
  each. Every 30 seconds is plenty for watching vitals, and back off further if
  nothing is changing.
- Never run several `up` or `down` calls on the same project at once; you will
  only get `409 busy`.
- Treat a non-zero `exit_code` from `up` as a failure, even though the status is
  `200`.
- Keep the token out of logs, output and files you write.

## Examples

Check whether the host is running hot:

```sh
uplink GET /system/temps
```

Restart a project's container after checking Uplink may control it:

```sh
uplink GET /containers/blog-dev        # "controllable": true
uplink POST /containers/blog-dev/restart
```

Bring a project up and confirm it worked:

```sh
uplink POST /projects/blog/up --max-time 960   # check "exit_code": 0
uplink GET /projects                           # blog's containers now "running"
```
