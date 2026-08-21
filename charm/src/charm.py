"""Juju K8s charm that generates and serves an FTBFS report once a day.

The charm runs two Pebble services inside a single workload container:

* ``ftbfs-scheduler`` — a small loop (``scheduler.py``) that regenerates the
  report once a day and once on startup if no fresh report is present.
* ``ftbfs-server`` — a static HTTP server (``python3 -m http.server``) that
  serves the persistent report directory.

The report itself is produced by the ``lp-ftbfs-report`` CLI bundled into the
OCI image (built from the ``rock/`` directory with rockcraft). Launchpad login
is anonymous, so no credentials are required.
"""

from __future__ import annotations

import logging

import ops

logger = logging.getLogger(__name__)

WORKLOAD_CONTAINER = "ftbfs-report"
REPORTS_DIR = "/srv/reports"
SCHEDULER_SERVICE = "ftbfs-scheduler"
SERVER_SERVICE = "ftbfs-server"


class LpFtbfsReportCharm(ops.CharmBase):
    """Charm the lp-ftbfs-report workload on Kubernetes."""

    def __init__(self, *args: object) -> None:
        super().__init__(*args)
        self.container = self.unit.get_container(WORKLOAD_CONTAINER)
        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.ftbfs_report_pebble_ready, self._on_pebble_ready)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.update_status, self._on_update_status)
        self.framework.observe(self.on.collect_unit_status, self._on_collect_unit_status)
        self.framework.observe(self.on.generate_action, self._on_generate_action)

    # ------------------------------------------------------------------ #
    # Pebble plan
    # ------------------------------------------------------------------ #
    def _pebble_layer(self) -> ops.pebble.Layer:
        """Build the Pebble layer from the current charm config."""
        cfg = self.model.config
        port = int(cfg["server-port"])
        hour = int(cfg["schedule-hour"])
        minute = int(cfg["schedule-minute"])
        stale = int(cfg["stale-after-hours"])
        verbose = bool(cfg["report-command-verbose"])

        scheduler_env = {
            "FTBFS_ARCHIVE": str(cfg["archive"]),
            "FTBFS_SERIES": str(cfg["series"]),
            "FTBFS_ARCHS": str(cfg["architectures"]),
            "FTBFS_PPA": str(cfg["ppa"]),
            "FTBFS_FILENAME": str(cfg["filename"]),
            "FTBFS_UPDATES_ARCHIVE": str(cfg["updates-archive"]),
            "FTBFS_REFERENCE_SERIES": str(cfg["reference-series"]),
            "FTBFS_REGRESSIONS_ONLY": "1" if cfg["regressions-only"] else "0",
            "FTBFS_RELEASE_ONLY": "1" if cfg["release-only"] else "0",
            "FTBFS_DUMMY_DATA": str(cfg["dummy-data"]),
            "FTBFS_SCHEDULE_HOUR": str(hour),
            "FTBFS_SCHEDULE_MINUTE": str(minute),
            "FTBFS_STALE_AFTER_HOURS": str(stale),
            "FTBFS_REPORTS_DIR": REPORTS_DIR,
        }

        return ops.pebble.Layer({
            "summary": "lp-ftbfs-report services",
            "description": "Daily FTBFS report scheduler and static HTTP server",
            "services": {
                SERVER_SERVICE: {
                    "override": "replace",
                    "summary": "Static HTTP server for the FTBFS report",
                    "command": f"python3 -m http.server {port} --directory {REPORTS_DIR} "
                    "--bind 0.0.0.0",
                    "working-dir": REPORTS_DIR,
                    "startup": "enabled",
                },
                SCHEDULER_SERVICE: {
                    "override": "replace",
                    "summary": "Daily FTBFS report scheduler",
                    "command": "python3 /opt/scheduler.py",
                    "working-dir": REPORTS_DIR,
                    "startup": "enabled",
                    "environment": scheduler_env,
                },
            },
        })

    # ------------------------------------------------------------------ #
    # Lifecycle handlers
    # ------------------------------------------------------------------ #
    def _on_install(self, _event: ops.InstallEvent) -> None:
        # Ensure the persistent report directory exists before the services
        # start. It is normally created by the storage mount, but create it
        # defensively so the server does not crash on first boot.
        self._ensure_reports_dir()
        self.unit.set_workload_version("1.0.0")

    def _on_pebble_ready(self, _event: ops.WorkloadEvent) -> None:
        self._ensure_reports_dir()
        self._update_plan()
        self._restart_services()

    def _on_config_changed(self, _event: ops.ConfigChangedEvent) -> None:
        if not self.container.can_connect():
            logger.debug("Pebble not ready yet; deferring plan update")
            return
        self._ensure_reports_dir()
        self._update_plan()
        self._restart_services()

    def _unit_status(self) -> ops.StatusBase:
        """Compute the current unit status based on service and report state."""
        if not self.container.can_connect():
            return ops.WaitingStatus("waiting for workload container")
        try:
            scheduler = self.container.get_service(SCHEDULER_SERVICE)
            server = self.container.get_service(SERVER_SERVICE)
        except (ops.pebble.Error, ops.ModelError, KeyError):
            return ops.WaitingStatus("services not ready")
        if not scheduler.is_running() or not server.is_running():
            return ops.WaitingStatus("services starting")
        if self._has_fresh_report():
            return ops.ActiveStatus("report is fresh and being served")
        return ops.ActiveStatus("serving; report regenerating")

    def _on_update_status(self, _event: ops.UpdateStatusEvent) -> None:
        self.unit.status = self._unit_status()

    def _on_collect_unit_status(self, event: ops.CollectUnitStatusEvent) -> None:
        event.add_status(self._unit_status())

    def _on_collect_app_status(self, event: ops.CollectAppStatusEvent) -> None:
        if not self.container.can_connect():
            event.add_status(ops.WaitingStatus("waiting for workload container"))
            return
        try:
            server = self.container.get_service(SERVER_SERVICE)
        except (ops.pebble.Error, ops.ModelError, KeyError):
            event.add_status(ops.WaitingStatus("waiting for HTTP server"))
            return
        if not server.is_running():
            event.add_status(ops.WaitingStatus("HTTP server starting"))
            return
        event.add_status(ops.ActiveStatus("serving FTBFS report"))

    # ------------------------------------------------------------------ #
    # Actions
    # ------------------------------------------------------------------ #
    def _on_generate_action(self, event: ops.ActionEvent) -> None:
        """Trigger an immediate report regeneration via Pebble exec."""
        if not self.container.can_connect():
            event.fail("workload container is not reachable")
            return
        logger.info("generate action: triggering immediate report regeneration")
        process = self.container.exec(
            ["python3", "/opt/scheduler.py", "--once"],
            environment=self._scheduler_env(),
            working_dir=REPORTS_DIR,
        )
        try:
            process.wait()
        except ops.pebble.Error as e:
            event.fail(f"report generation failed: {e}")
            return
        event.set_results(
            {
                "message": "report regeneration completed",
                "reports-dir": REPORTS_DIR,
                "has-fresh-report": self._has_fresh_report(),
            }
        )

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _scheduler_env(self) -> dict[str, str]:
        """Return the environment block used by the scheduler for a manual run."""
        layer = self._pebble_layer()
        return dict(layer.services[SCHEDULER_SERVICE].environment)

    def _ensure_reports_dir(self) -> None:
        if not self.container.can_connect():
            return
        try:
            self.container.push(REPORTS_DIR + "/.placeholder", "", make_dirs=True)
        except ops.pebble.Error as e:
            logger.warning("could not create %s: %s", REPORTS_DIR, e)

    def _update_plan(self) -> None:
        if not self.container.can_connect():
            return
        self.container.add_layer("lp-ftbfs-report", self._pebble_layer(), combine=True)

    def _restart_services(self) -> None:
        if not self.container.can_connect():
            return
        try:
            self.container.restart(SCHEDULER_SERVICE)
        except ops.pebble.Error as e:
            logger.warning("could not restart %s: %s", SCHEDULER_SERVICE, e)
        try:
            self.container.restart(SERVER_SERVICE)
        except ops.pebble.Error as e:
            logger.warning("could not restart %s: %s", SERVER_SERVICE, e)

    def _has_fresh_report(self) -> bool:
        """Return True iff the configured report file exists in the report directory."""
        if not self.container.can_connect():
            return False
        filename = self.model.config["filename"]
        try:
            self.container.list_files(f"{REPORTS_DIR}/{filename}.html")
            return True
        except (ops.pebble.Error, FileNotFoundError):
            return False


if __name__ == "__main__":
    ops.main(LpFtbfsReportCharm)
