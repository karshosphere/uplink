import time
from datetime import UTC, datetime

import psutil

CPU_SAMPLE_SECONDS = 0.2


def temps() -> dict:
    read = getattr(psutil, "sensors_temperatures", None)
    readings = read() if read else {}
    return {
        "sensors": {
            chip: [
                {"label": s.label, "current": s.current, "high": s.high, "critical": s.critical}
                for s in sensors
            ]
            for chip, sensors in readings.items()
        }
    }


def memory() -> dict:
    ram = psutil.virtual_memory()
    swap = psutil.swap_memory()
    return {
        "ram": {"total": ram.total, "used": ram.used, "available": ram.available, "percent": ram.percent},
        "swap": {"total": swap.total, "used": swap.used, "free": swap.free, "percent": swap.percent},
    }


def cpu() -> dict:
    per_core = psutil.cpu_percent(interval=CPU_SAMPLE_SECONDS, percpu=True)
    one, five, fifteen = psutil.getloadavg()
    return {
        "count": psutil.cpu_count(),
        "percent": round(sum(per_core) / len(per_core), 1) if per_core else 0.0,
        "per_core": per_core,
        "load_average": {"1m": one, "5m": five, "15m": fifteen},
    }


def _mount_usage(mount: str) -> dict:
    try:
        usage = psutil.disk_usage(mount)
    except OSError as e:
        return {"mount": mount, "error": e.strerror or str(e)}
    return {"mount": mount, "total": usage.total, "used": usage.used, "free": usage.free, "percent": usage.percent}


def disk(mounts: tuple[str, ...]) -> dict:
    return {"mounts": [_mount_usage(m) for m in mounts]}


def uptime() -> dict:
    boot = psutil.boot_time()
    return {
        "boot_time": datetime.fromtimestamp(boot, UTC).isoformat(timespec="seconds"),
        "uptime_seconds": int(time.time() - boot),
    }


def everything(mounts: tuple[str, ...]) -> dict:
    return {
        "temps": temps(),
        "memory": memory(),
        "cpu": cpu(),
        "disk": disk(mounts),
        "uptime": uptime(),
    }
