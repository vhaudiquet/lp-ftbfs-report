"""Tests for the SPPH.BuildLog model (buildstate mapping + tooltip)."""

from __future__ import annotations

from datetime import datetime, timezone

from lp_ftbfs_report.fetchers.base import BuildRecord
from lp_ftbfs_report.models import SPPH


def _record(
    *,
    buildstate: str = "Failed to build",
    datebuilt: datetime | None,
    dependencies: str | None = None,
) -> BuildRecord:
    return BuildRecord(
        source_package_name="pkg",
        source_package_version="1.0-1",
        arch_tag="amd64",
        buildstate=buildstate,
        datebuilt=datebuilt,
        current_source_publication_link="https://example/pub",
        build_log_url="https://example/log",
        upload_log_url=None,
        dependencies=dependencies,
        self_link="https://example/self",
    )


def test_buildlog_depwait_tooltip_lists_dependencies():
    """DepWait-family states produce a "waits on <deps>" tooltip."""
    build = _record(
        buildstate="Dependency wait",
        datebuilt=datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc),
        dependencies="libfoo-dev (>= 1.0)",
    )
    log = SPPH.BuildLog(build, never_built=False, no_regression=False)
    assert log.buildstate == "MANUALDEPWAIT"
    assert log.tooltip == "waits on libfoo-dev (>= 1.0)"


def test_buildlog_broken_build_tooltip_when_datebuilt_is_none():
    """A None datebuilt yields "Broken build" (not "Build finish unknown")."""
    build = _record(buildstate="Failed to build", datebuilt=None)
    log = SPPH.BuildLog(build, never_built=False, no_regression=False)
    assert log.buildstate == "FAILEDTOBUILD"
    assert log.tooltip == "Broken build"


def test_buildlog_finished_on_tooltip_when_datebuilt_present():
    """A real datebuilt yields the formatted "Build finished on ..." tooltip."""
    when = datetime(2026, 4, 1, 12, 30, 45, tzinfo=timezone.utc)
    build = _record(buildstate="Failed to build", datebuilt=when)
    log = SPPH.BuildLog(build, never_built=False, no_regression=False)
    assert log.tooltip == "Build finished on 2026-04-01 12:30:45 UTC"


def test_buildlog_never_built_overrides_ftbfs():
    """never_built rewrites a Failed to build state to ALWAYSFTBFS."""
    build = _record(
        buildstate="Failed to build",
        datebuilt=datetime(2026, 4, 1, tzinfo=timezone.utc),
    )
    log = SPPH.BuildLog(build, never_built=True, no_regression=False)
    assert log.buildstate == "ALWAYSFTBFS"


def test_buildlog_no_regression_overrides_ftbfs():
    """no_regression rewrites a Failed to build state to NOREGRFTBFS."""
    build = _record(
        buildstate="Failed to build",
        datebuilt=datetime(2026, 4, 1, tzinfo=timezone.utc),
    )
    log = SPPH.BuildLog(build, never_built=False, no_regression=True)
    assert log.buildstate == "NOREGRFTBFS"


def test_buildlog_never_built_takes_precedence_over_no_regression():
    """For FTBFS, no_regression is applied first and wins over never_built.

    never_built only rewrites a state that is still "FAILEDTOBUILD"; after
    no_regression rewrites it to NOREGRFTBFS, the never_built branch no longer
    matches. This pins that ordering so a future refactor does not silently
    flip it.
    """
    build = _record(
        buildstate="Failed to build",
        datebuilt=datetime(2026, 4, 1, tzinfo=timezone.utc),
    )
    log = SPPH.BuildLog(build, never_built=True, no_regression=True)
    assert log.buildstate == "NOREGRFTBFS"
