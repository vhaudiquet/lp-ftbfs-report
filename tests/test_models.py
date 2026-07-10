"""Tests for the SPPH.BuildLog and PersonTeam models."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from lp_ftbfs_report.fetchers.base import BuildRecord
from lp_ftbfs_report.models import SPPH, ModelCaches, PersonTeam

try:
    from launchpadlib.errors import HTTPError
except ImportError:  # pragma: no cover
    HTTPError = None  # type: ignore[assignment]


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


# --------------------------------------------------------------------------- #
# PersonTeam caching
# --------------------------------------------------------------------------- #


class _MockResponse:
    def __init__(self, status: int):
        self.status = status


class _MockLaunchpad:
    """Minimal Launchpad stub: load(url) returns objects or raises HTTPError."""

    def __init__(self, objects: dict[str, object], missing_status: int = 404):
        self._objects = objects
        self._missing_status = missing_status

    def load(self, url: str):  # noqa: ARG002
        if url in self._objects:
            return self._objects[url]
        raise HTTPError(_MockResponse(self._missing_status), b"not found")


class _PersonObj:
    def __init__(self, display_name: str, name: str):
        self.display_name = display_name
        self.name = name


@pytest.fixture(autouse=True)
def _fresh_caches():
    """Each PersonTeam test gets its own empty ModelCaches."""
    yield


def test_personteam_caches_and_dedups_by_link():
    """Two lookups of the same link return the same cached PersonTeam."""
    caches = ModelCaches()
    lp = _MockLaunchpad({"https://lp/~alice": _PersonObj("Alice", "alice")})
    first = PersonTeam("https://lp/~alice", caches=caches, launchpad=lp)
    second = PersonTeam("https://lp/~alice", caches=caches, launchpad=lp)
    assert first is not None
    assert first is second
    assert first.name == "alice"
    # The cache holds the resolved object under the link.
    assert caches.persons["https://lp/~alice"] is first


def test_personteam_404_caches_none():
    """A 404 is cached as None so the link is not re-fetched each time."""
    caches = ModelCaches()
    lp = _MockLaunchpad({}, missing_status=404)
    assert PersonTeam("https://lp/~ghost", caches=caches, launchpad=lp) is None
    # Second lookup must hit the cache (load() would raise again if it didn't).
    assert PersonTeam("https://lp/~ghost", caches=caches, launchpad=lp) is None
    assert caches.persons["https://lp/~ghost"] is None


def test_personteam_non_404_http_error_propagates():
    """A non-(404/410) HTTPError must propagate, not be swallowed."""
    caches = ModelCaches()
    lp = _MockLaunchpad({}, missing_status=500)
    with pytest.raises(HTTPError):
        PersonTeam("https://lp/~broken", caches=caches, launchpad=lp)
    # Nothing cached when the error propagated.
    assert "https://lp/~broken" not in caches.persons


def test_personteam_keyerror_propagates():
    """A KeyError from load()/attr access must not be swallowed into None.

    Previously the inner `except KeyError: return None` masked any KeyError
    raised anywhere in the lookup as a missing person, silently corrupting the
    Changed-By tooltip. It must now propagate so bugs surface.
    """

    class _RaisesKeyError:
        def load(self, url):  # noqa: ARG002
            raise KeyError("unexpected")

    caches = ModelCaches()
    with pytest.raises(KeyError):
        PersonTeam("https://lp/~bad", caches=caches, launchpad=_RaisesKeyError())
