#!/usr/bin/env python3
"""Daily scheduler for the FTBFS report charm.

Runs inside the workload container and is launched by Pebble as the
``ftbfs-scheduler`` service. It:

1. Regenerates the report once on startup if no fresh report exists
   (configurable via ``FTBFS_STALE_AFTER_HOURS``).
2. Sleeps until the next configured UTC ``HH:MM`` and regenerates, in a loop.

When invoked with ``--once`` it regenerates exactly once and exits; this is
used by the charm's ``generate`` action.

Configuration is taken from environment variables (see ``_env``), which are
populated by the Pebble plan from charm config options.
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
import shutil
import subprocess
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s scheduler: %(message)s",
)
log = logging.getLogger("ftbfs-scheduler")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    return _env(name, "1" if default else "0") in ("1", "true", "yes", "on")


def build_command(report_dir: str) -> list[str]:
    """Build the lp-ftbfs-report command line from the environment."""
    cmd: list[str] = [sys.executable, "-m", "lp_ftbfs_report.build_status"]

    ppa = _env("FTBFS_PPA")
    if ppa:
        cmd += ["--ppa", ppa]

    updates = _env("FTBFS_UPDATES_ARCHIVE")
    if updates:
        cmd += ["--updates-archive", updates]

    ref = _env("FTBFS_REFERENCE_SERIES")
    if ref:
        cmd += ["--reference-series", ref]

    if _env_bool("FTBFS_REGRESSIONS_ONLY"):
        cmd.append("--regressions-only")
    if _env_bool("FTBFS_RELEASE_ONLY"):
        cmd.append("--release-only")
    if _env_bool("FTBFS_VERBOSE"):
        cmd.append("--verbose")

    dummy = _env("FTBFS_DUMMY_DATA")
    if dummy:
        cmd += ["--dummy-data", dummy]
    filename = _env("FTBFS_FILENAME", "index")
    cmd += ["--filename", filename, "--output-dir", report_dir]

    # Positional args depend on mode:
    #   PPA / dummy : <series> <arch> [arch ...]
    #   standard    : <archive> <series> <arch> [arch ...]
    series = _env("FTBFS_SERIES")
    archs = [a for a in _env("FTBFS_ARCHS").split(",") if a]
    if not series or not archs:
        raise RuntimeError("FTBFS_SERIES and FTBFS_ARCHS must be set")
    if not ppa and not dummy:
        archive = _env("FTBFS_ARCHIVE", "primary")
        cmd += [archive, series]
    else:
        cmd += [series]
    cmd += archs
    return cmd


def index_path(report_dir: str) -> str:
    return os.path.join(report_dir, f"{_env('FTBFS_FILENAME', 'index')}.html")


def is_fresh(report_dir: str, stale_after_hours: int) -> bool:
    """Return True iff the report exists and is younger than the threshold."""
    path = index_path(report_dir)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return False
    age = _dt.datetime.now(_dt.timezone.utc).timestamp() - mtime
    return age <= stale_after_hours * 3600


def generate(report_dir: str) -> int:
    """Run lp-ftbfs-report and return its exit code."""
    cmd = build_command(report_dir)
    log.info("running: %s", " ".join(cmd))
    started = _dt.datetime.now(_dt.timezone.utc)
    try:
        proc = subprocess.run(cmd, check=False)  # noqa: S603
    except FileNotFoundError as e:
        log.error("lp-ftbfs-report not found in image: %s", e)
        return 127
    elapsed = (_dt.datetime.now(_dt.timezone.utc) - started).total_seconds()
    if proc.returncode == 0:
        log.info("report generated in %.1fs", elapsed)
    else:
        log.error("lp-ftbfs-report exited with %d after %.1fs", proc.returncode, elapsed)
    return proc.returncode


def seconds_until_next_run(hour: int, minute: int) -> float:
    """Seconds to wait until the next UTC HH:MM."""
    now = _dt.datetime.now(_dt.timezone.utc)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += _dt.timedelta(days=1)
    return (target - now).total_seconds()


def main() -> int:
    report_dir = _env("FTBFS_REPORTS_DIR", "/srv/reports")
    os.makedirs(report_dir, exist_ok=True)

    once = "--once" in sys.argv

    stale_after = _env_int("FTBFS_STALE_AFTER_HOURS", 24)
    hour = _env_int("FTBFS_SCHEDULE_HOUR", 2)
    minute = _env_int("FTBFS_SCHEDULE_MINUTE", 0)
    hour = max(0, min(23, hour))
    minute = max(0, min(59, minute))

    if once:
        return generate(report_dir)

    # Startup: regenerate only if no fresh report is present.
    if stale_after > 0 and not is_fresh(report_dir, stale_after):
        log.info("no fresh report; generating on startup")
        generate(report_dir)
    else:
        log.info("fresh report present; skipping startup generation")

    log.info(
        "scheduling daily runs at %02d:%02d UTC (report dir: %s)",
        hour,
        minute,
        report_dir,
    )

    # Sanity check: the tool must be importable.
    if shutil.which("lp-ftbfs-report") is None and not os.path.exists(
        os.path.join(sys.prefix, "bin", "lp-ftbfs-report")
    ):
        # Not fatal: the entry point may resolve differently; we rely on the
        # python -m invocation in build_command().
        pass

    while True:
        wait = seconds_until_next_run(hour, minute)
        log.info("next run in %.0f seconds", wait)
        try:
            time.sleep(wait)
        except KeyboardInterrupt:
            return 0
        generate(report_dir)


if __name__ == "__main__":
    sys.exit(main())
