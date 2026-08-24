"""Integration tests for the fetch_pkg_list -> model cache pipeline.

These tests drive the full data_fetcher.fetch_pkg_list pipeline against a
DummyFetcher wired to its mock Launchpad (which routes load()/searchTasks
back to the fetcher). They lock in the deduplication/caching contract of
the model classes (SPPH, SourcePackage, PersonTeam), which currently rely
on class-level caches keyed by publication link / source name / person
link. A future refactor that moves those caches off module globals must
preserve this behaviour.
"""

from __future__ import annotations

import json

from lp_ftbfs_report.data_fetcher import FetchContext, ReportAccumulators, fetch_pkg_list
from lp_ftbfs_report.fetchers import DummyFetcher
from lp_ftbfs_report.models import ModelCaches, SourcePackage

ARCHES = ["amd64", "arm64"]


def _build_context(sample_fixture_path: str) -> tuple:
    """Build a fetcher + mock launchpad, the run context and accumulators.

    Mirrors build_status.main()'s dummy-mode wiring.
    """
    fetcher = DummyFetcher(sample_fixture_path, api_version="devel")
    launchpad = fetcher.create_mock_launchpad()
    ubuntu = launchpad  # mirrors build_status.py dummy-mode wiring

    components: dict[str, list[SourcePackage]] = {
        "main": [],
        "restricted": [],
        "universe": [],
        "multiverse": [],
    }
    packagesets = fetcher.get_packagesets()
    packagesets_ftbfs: dict[str, list[SourcePackage]] = {ps: [] for ps in packagesets}
    teams = fetcher.get_teams()
    teams_ftbfs: dict[str, list[SourcePackage]] = {team: [] for team in teams}

    ctx = FetchContext(
        launchpad=launchpad,
        ubuntu=ubuntu,
        main_archive=None,
        ref_series=None,
        find_tagged_bugs="ftbfs",
        caches=ModelCaches(),
        api_version="devel",
        verbose=False,
        regressions_only=False,
    )
    accumulators = ReportAccumulators(
        components=components,
        packagesets=packagesets,
        packagesets_ftbfs=packagesets_ftbfs,
        teams=teams,
        teams_ftbfs=teams_ftbfs,
    )
    return fetcher, ctx, accumulators, components, packagesets_ftbfs, teams_ftbfs


def _names(pkgs: list[SourcePackage]) -> list[str]:
    return sorted(p.name for p in pkgs)


def test_fetch_pkg_list_dedups_shared_publication_and_populates_accumulators(
    sample_fixture_path,
):
    """Two build records sharing one publication link collapse to one SPPH.

    example-pkg fails on both amd64 and arm64, and both records point at the
    same current_source_publication_link (+sourcepub/1). The pipeline must
    therefore create a single SourcePackage with a single version (one cached
    SPPH) carrying two build logs (amd64 + arm64), rather than two separate
    versions each with one log.
    """
    fetcher, ctx, accumulators, components, packagesets_ftbfs, teams_ftbfs = _build_context(
        sample_fixture_path
    )

    # "Failed to build": example-pkg (amd64, arm64) + always-fail-pkg (amd64)
    fetch_pkg_list(
        state="Failed to build",
        arch_list=ARCHES,
        fetcher=fetcher,
        accumulators=accumulators,
        ctx=ctx,
    )
    # "Dependency wait": depwait-pkg (amd64), component main
    fetch_pkg_list(
        state="Dependency wait",
        arch_list=ARCHES,
        fetcher=fetcher,
        accumulators=accumulators,
        ctx=ctx,
    )

    # --- components populated correctly ---
    assert _names(components["universe"]) == ["always-fail-pkg", "example-pkg"]
    assert _names(components["main"]) == ["depwait-pkg"]
    assert components["restricted"] == []
    assert components["multiverse"] == []

    # --- SPPH dedup: example-pkg has ONE version with TWO build logs ---
    example_pkg = next(p for p in components["universe"] if p.name == "example-pkg")
    assert len(example_pkg.versions) == 1
    spph = example_pkg.versions[0]
    assert set(spph.logs.keys()) == {"amd64", "arm64"}
    # The cached SPPH for that publication link is the very same object.
    pub_link = "https://api.launchpad.net/devel/ubuntu/+archive/test/+sourcepub/1"
    assert ctx.caches.spphs[pub_link] is spph

    # --- SourcePackage dedup: one SourcePackage per name, cached ---
    assert ctx.caches.sources["example-pkg"] is example_pkg
    assert ctx.caches.sources["depwait-pkg"] is components["main"][0]

    # --- bug loading wired through the mock launchpad ---
    example_bugs = [b.id for b in example_pkg.tagged_bugs]
    assert 123456 in example_bugs

    # --- packagesets / teams populated for the main-component package ---
    assert _names(packagesets_ftbfs["server"]) == ["depwait-pkg"]
    assert _names(teams_ftbfs["server-team"]) == ["depwait-pkg"]
    # empty packagesets/teams stay empty
    assert packagesets_ftbfs["desktop"] == []
    assert teams_ftbfs["foundations-team"] == []


def test_personteam_is_cached_and_shared_across_publications(sample_fixture_path):
    """All publications share one package_creator_link -> one PersonTeam.

    The three sourcepubs in the fixture all point at the same creator link,
    so PersonTeam.__new__ must return the same cached object for each SPPH.
    """
    fetcher, ctx, accumulators, _components, _packagesets_ftbfs, _teams_ftbfs = _build_context(
        sample_fixture_path
    )

    fetch_pkg_list(
        state="Failed to build",
        arch_list=ARCHES,
        fetcher=fetcher,
        accumulators=accumulators,
        ctx=ctx,
    )
    fetch_pkg_list(
        state="Dependency wait",
        arch_list=ARCHES,
        fetcher=fetcher,
        accumulators=accumulators,
        ctx=ctx,
    )

    spph1 = ctx.caches.spphs["https://api.launchpad.net/devel/ubuntu/+archive/test/+sourcepub/1"]
    spph2 = ctx.caches.spphs["https://api.launchpad.net/devel/ubuntu/+archive/test/+sourcepub/2"]
    spph3 = ctx.caches.spphs["https://api.launchpad.net/devel/ubuntu/+archive/test/+sourcepub/3"]
    # One PersonTeam cached for the shared creator link, referenced by all SPPHs.
    assert spph1.changed_by is spph2.changed_by
    assert spph2.changed_by is spph3.changed_by
    creator_link = "https://api.launchpad.net/devel/~test-user"
    assert ctx.caches.persons[creator_link] is spph1.changed_by


def test_caches_are_cleared_between_runs(sample_fixture_path):
    """clear() empties the model caches so a fresh run does not see stale data.

    This mirrors the start-of-run cache reset in build_status.main() and is
    the contract any future cache refactor must preserve.
    """
    fetcher, ctx, accumulators, _components, _packagesets_ftbfs, _teams_ftbfs = _build_context(
        sample_fixture_path
    )

    fetch_pkg_list(
        state="Failed to build",
        arch_list=ARCHES,
        fetcher=fetcher,
        accumulators=accumulators,
        ctx=ctx,
    )
    assert ctx.caches.spphs  # populated during the run
    assert ctx.caches.sources

    ctx.caches.clear()

    assert ctx.caches.spphs == {}
    assert ctx.caches.sources == {}
    assert ctx.caches.persons == {}


# --------------------------------------------------------------------------- #
# updates-archive integration (cross-fetcher)
# --------------------------------------------------------------------------- #


def test_update_archive_success_skips_main_archive_failures_across_fetchers(
    tmp_path,
):
    """A build that succeeded in the updates archive is skipped during the
    main-archive pass, even though the two passes use different fetcher
    instances.

    The update_builds dict lives on the shared FetchContext so both passes
    see it.  Before the fix, update_builds was per-fetcher-instance and the
    check always returned False — a regression introduced when the module-
    level global was moved onto TestRebuildFetcher in the fetcher abstraction.
    """
    fixture = {
        "archive": {"name": "test", "displayname": "Test Archive"},
        "series": {"name": "oracular", "fullseriesname": "Ubuntu Oracular"},
        "builds": [
            {
                "source_package_name": "fixed-pkg",
                "source_package_version": "1.0-1",
                "arch_tag": "amd64",
                "buildstate": "Successfully built",
                "datebuilt": "2026-04-01T12:00:00+00:00",
                "current_source_publication_link": "",
                "build_log_url": None,
                "upload_log_url": None,
                "dependencies": None,
                "self_link": "https://api.launchpad.net/devel/~test/+build/1",
            },
            {
                "source_package_name": "fixed-pkg",
                "source_package_version": "1.0-1",
                "arch_tag": "amd64",
                "buildstate": "Failed to build",
                "datebuilt": "2026-04-02T12:00:00+00:00",
                "current_source_publication_link": (
                    "https://api.launchpad.net/devel/ubuntu/+archive/test/+sourcepub/1"
                ),
                "build_log_url": "https://launchpad.net/~test/+build/2/+files/log.txt.gz",
                "upload_log_url": None,
                "dependencies": None,
                "self_link": "https://api.launchpad.net/devel/~test/+build/2",
            },
            {
                "source_package_name": "still-broken-pkg",
                "source_package_version": "2.0-1",
                "arch_tag": "amd64",
                "buildstate": "Failed to build",
                "datebuilt": "2026-04-02T12:00:00+00:00",
                "current_source_publication_link": (
                    "https://api.launchpad.net/devel/ubuntu/+archive/test/+sourcepub/2"
                ),
                "build_log_url": "https://launchpad.net/~test/+build/3/+files/log.txt.gz",
                "upload_log_url": None,
                "dependencies": None,
                "self_link": "https://api.launchpad.net/devel/~test/+build/3",
            },
        ],
        "publications": {
            "https://api.launchpad.net/devel/ubuntu/+archive/test/+sourcepub/2": {
                "source_package_name": "still-broken-pkg",
                "source_package_version": "2.0-1",
                "component_name": "universe",
                "pocket": "Release",
                "package_creator_link": "https://api.launchpad.net/devel/~creator",
            },
        },
        "packagesets": {},
        "teams": {},
        "bugs": {},
    }
    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(json.dumps(fixture))

    # Two independent fetcher instances, as in build_status.main().
    updates_fetcher = DummyFetcher(str(fixture_path), api_version="devel")
    main_fetcher = DummyFetcher(str(fixture_path), api_version="devel")

    components: dict[str, list[SourcePackage]] = {
        "main": [],
        "restricted": [],
        "universe": [],
        "multiverse": [],
    }
    packagesets = main_fetcher.get_packagesets()
    packagesets_ftbfs: dict[str, list[SourcePackage]] = {ps: [] for ps in packagesets}
    teams = main_fetcher.get_teams()
    teams_ftbfs: dict[str, list[SourcePackage]] = {team: [] for team in teams}

    ctx = FetchContext(
        launchpad=main_fetcher.create_mock_launchpad(),
        ubuntu=main_fetcher.create_mock_launchpad(),
        main_archive=None,
        ref_series=None,
        find_tagged_bugs="ftbfs",
        caches=ModelCaches(),
        api_version="devel",
        verbose=False,
        regressions_only=False,
    )
    accumulators = ReportAccumulators(
        components=components,
        packagesets=packagesets,
        packagesets_ftbfs=packagesets_ftbfs,
        teams=teams,
        teams_ftbfs=teams_ftbfs,
    )
    arch_list = ["amd64"]

    # Phase 1 — updates-archive pass: record successful builds.
    fetch_pkg_list(
        state="Successfully built",
        arch_list=arch_list,
        fetcher=updates_fetcher,
        accumulators=accumulators,
        ctx=ctx,
        is_updates_archive=True,
    )
    assert ("fixed-pkg", "amd64") in ctx.update_builds

    # Phase 2 — main-archive pass: fixed-pkg is skipped, still-broken-pkg is kept.
    fetch_pkg_list(
        state="Failed to build",
        arch_list=arch_list,
        fetcher=main_fetcher,
        accumulators=accumulators,
        ctx=ctx,
    )
    assert _names(components["universe"]) == ["still-broken-pkg"]
