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
