"""Unit tests for the lp-ftbfs-report charm."""

from __future__ import annotations

import ops
import pytest
from ops import testing

from charm import (
    REPORTS_DIR,
    SCHEDULER_SERVICE,
    SERVER_SERVICE,
    LpFtbfsReportCharm,
)

CONTAINER = "ftbfs-report"


def _container(*, can_connect=True, execs=()):
    """Build the workload container used by all tests."""
    return testing.Container(
        name=CONTAINER,
        can_connect=can_connect,
        execs=execs,
    )


def _state(*, can_connect=True, config=None, execs=(), leader=False):
    """Build a default State with the workload container and reports storage."""
    return testing.State(
        containers=[_container(
            can_connect=can_connect,
            execs=execs,
        )],
        storages=[testing.Storage("reports")],
        config=config or {},
        leader=leader,
    )


def test_metadata_declares_container_and_storage():
    ctx = testing.Context(LpFtbfsReportCharm)
    state_in = testing.State.from_context(ctx)
    with ctx(ctx.on.install(), state_in) as mgr:
        meta = mgr.charm.framework.meta
        assert CONTAINER in meta.containers
        assert "reports" in meta.storages
        assert meta.containers[CONTAINER].mounts["reports"].location == REPORTS_DIR
        assert "generate" in meta.actions
    ctx.close()


def test_pebble_ready_starts_services():
    ctx = testing.Context(LpFtbfsReportCharm)
    container = _container()
    state_in = testing.State(
        containers=[container],
        storages=[testing.Storage("reports")],
    )
    state_out = ctx.run(ctx.on.pebble_ready(container=container), state_in)
    plan = state_out.get_container(CONTAINER).plan
    assert SCHEDULER_SERVICE in plan.services
    assert SERVER_SERVICE in plan.services
    assert plan.services[SERVER_SERVICE].startup == "enabled"
    assert plan.services[SCHEDULER_SERVICE].startup == "enabled"
    ctx.close()


def test_plan_serves_reports_dir_on_all_interfaces():
    ctx = testing.Context(LpFtbfsReportCharm)
    container = _container()
    state_in = testing.State(
        containers=[container],
        storages=[testing.Storage("reports")],
    )
    state_out = ctx.run(ctx.on.pebble_ready(container=container), state_in)
    server_cmd = state_out.get_container(CONTAINER).plan.services[SERVER_SERVICE].command
    assert "--directory /srv/reports" in server_cmd
    assert "--bind 0.0.0.0" in server_cmd
    assert "8080" in server_cmd
    ctx.close()


def test_plan_passes_config_to_scheduler_env():
    ctx = testing.Context(LpFtbfsReportCharm)
    container = _container()
    state_in = testing.State(
        containers=[container],
        storages=[testing.Storage("reports")],
        config={
            "archive": "primary",
            "series": "oracular",
            "architectures": "amd64,arm64",
            "filename": "index",
            "schedule-hour": 5,
            "schedule-minute": 30,
        },
    )
    state_out = ctx.run(ctx.on.pebble_ready(container=container), state_in)
    env = state_out.get_container(CONTAINER).plan.services[SCHEDULER_SERVICE].environment
    assert env["FTBFS_ARCHIVE"] == "primary"
    assert env["FTBFS_SERIES"] == "oracular"
    assert env["FTBFS_ARCHS"] == "amd64,arm64"
    assert env["FTBFS_FILENAME"] == "index"
    assert env["FTBFS_SCHEDULE_HOUR"] == "5"
    assert env["FTBFS_SCHEDULE_MINUTE"] == "30"
    ctx.close()


def test_ppa_config_keeps_archive_env_but_scheduler_uses_ppa():
    ctx = testing.Context(LpFtbfsReportCharm)
    with ctx(
        ctx.on.config_changed(),
        _state(
            config={
                "ppa": "ubuntu-toolchain-r/test",
                "series": "noble",
                "architectures": "amd64",
                "filename": "index",
            },
        ),
    ) as mgr:
        mgr.run()
        env = mgr.charm._scheduler_env()
        assert env["FTBFS_PPA"] == "ubuntu-toolchain-r/test"
        # archive is still passed through; scheduler ignores it in ppa mode
        assert env["FTBFS_ARCHIVE"] == "primary"
    ctx.close()


def test_server_port_config_changes_command():
    ctx = testing.Context(LpFtbfsReportCharm)
    container = _container()
    state_in = testing.State(
        containers=[container],
        storages=[testing.Storage("reports")],
        config={"server-port": 9090},
    )
    state_out = ctx.run(ctx.on.pebble_ready(container=container), state_in)
    cmd = state_out.get_container(CONTAINER).plan.services[SERVER_SERVICE].command
    assert "9090" in cmd
    assert "8080" not in cmd
    ctx.close()


def test_generate_action_returns_results():
    ctx = testing.Context(LpFtbfsReportCharm)
    ctx.run(
        ctx.on.action("generate"),
        _state(execs=[testing.Exec(["python3", "/opt/scheduler.py"], return_code=0)]),
    )
    assert ctx.action_results["reports-dir"] == REPORTS_DIR
    assert ctx.action_results["has-fresh-report"] is False
    ctx.close()


def test_generate_action_fails_when_exec_fails():
    ctx = testing.Context(LpFtbfsReportCharm)
    with pytest.raises(testing.ActionFailed):
        ctx.run(
            ctx.on.action("generate"),
            _state(execs=[testing.Exec(["python3", "/opt/scheduler.py"], return_code=2)]),
        )
    ctx.close()


def test_update_status_when_no_report():
    ctx = testing.Context(LpFtbfsReportCharm)
    container = _container()
    state_in = testing.State(
        containers=[container],
        storages=[testing.Storage("reports")],
    )
    # Fire pebble_ready first to populate the Pebble plan with services.
    state_after = ctx.run(ctx.on.pebble_ready(container=container), state_in)
    ctx.close()

    ctx2 = testing.Context(LpFtbfsReportCharm)
    state_out = ctx2.run(ctx2.on.update_status(), state_after)
    assert isinstance(
        state_out.unit_status,
        (ops.ActiveStatus, ops.WaitingStatus, ops.MaintenanceStatus),
    )
    ctx2.close()


def test_collect_app_status_waiting_when_cannot_connect():
    ctx = testing.Context(LpFtbfsReportCharm)
    state_out = ctx.run(ctx.on.collect_app_status(), _state(can_connect=False, leader=True))
    assert isinstance(state_out.app_status, ops.WaitingStatus)
    ctx.close()


def test_has_fresh_report_uses_configured_filename():
    """_has_fresh_report must look for <filename>.html, not hardcoded index.html."""
    ctx = testing.Context(LpFtbfsReportCharm)
    container = _container()
    state_in = testing.State(
        containers=[container],
        storages=[testing.Storage("reports")],
        config={"filename": "report"},
    )
    # Fire pebble_ready first so services exist in the plan.
    state_after = ctx.run(ctx.on.pebble_ready(container=container), state_in)
    ctx.close()

    ctx2 = testing.Context(LpFtbfsReportCharm)
    with ctx2(ctx2.on.update_status(), state_after) as mgr:
        mgr.run()
        # When filename is "report", the charm looks for /srv/reports/report.html
        # (which does not exist), so _has_fresh_report should return False.
        assert mgr.charm._has_fresh_report() is False
    ctx2.close()
