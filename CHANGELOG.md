# Changelog

## added/modified:

* feature/skeleton: Unix socket server with token auth, audit log, config file, health check and a systemd user service.
* feature/system-stats: read-only routes for CPU temperature and load, RAM, swap, disk and uptime.
* feature/container-routes: list every container, and start, stop or restart those belonging to a project, never protected ones.
* feature/project-routes: list the projects in ~/Projects and bring them up and down through the host's d script.
* feature/protected-projects: Judy's own project in ~/Projects/Judy and its containers are off-limits to every action.
