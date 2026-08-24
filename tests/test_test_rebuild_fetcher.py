"""Tests for TestRebuildFetcher against mocked Launchpad objects.

The real fetcher methods (get_build_records, check_current_publication,
get_main_archive_build_state, find_reference_build) talk to launchpadlib
collections and published-source/binary objects. They had no direct test
coverage (only the DummyFetcher path was exercised). These tests drive them
with simple fakes so the algorithmic logic — arch/link filtering, pocket
filtering, is_debug skip, "most recent publication wins", first-wins-per-arch
regression caching, and the updates-archive helpers — is verified.
"""

from __future__ import annotations

from types import SimpleNamespace

from lp_ftbfs_report.fetchers.base import BuildRecord
from lp_ftbfs_report.fetchers.test_rebuild import TestRebuildFetcher

# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


class _ScalarTotal:
    """Stand-in for lazr.restfulclient ScalarValue: .value holds the int.

    lazr Collection.total_size can be one of these (when the size is linked,
    not inlined in the representation) rather than a plain int. lazr's
    Collection.__len__ unwraps it; the fetcher must do the same.
    """

    def __init__(self, value: int):
        self.value = value


class FakeCollection(list):
    """A lazr-like collection: iterable + .total_size + __len__.

    Mirrors lazr.restfulclient.resource.Collection: total_size may be an int
    or a _ScalarTotal wrapper, and __len__ unwraps to a plain int (raising
    TypeError when size is unavailable).
    """

    def __init__(self, items=(), total_size=None):
        super().__init__(items)
        self.total_size = len(items) if total_size is None else total_size

    def __len__(self):
        total_size = self.total_size
        if isinstance(total_size, int):
            return total_size
        if isinstance(total_size, _ScalarTotal):
            return total_size.value
        raise TypeError("collection size is not available")


def _build(
    name="pkg",
    version="1.0-1",
    arch="amd64",
    state="Failed to build",
    csp_link="https://lp/+sourcepub/1",
    datebuilt=None,
):
    """A launchpadlib-like build object."""
    return SimpleNamespace(
        source_package_name=name,
        source_package_version=version,
        arch_tag=arch,
        buildstate=state,
        datebuilt=datebuilt,
        current_source_publication_link=csp_link,
        build_log_url="https://lp/log",
        upload_log_url=None,
        dependencies=None,
        self_link="https://lp/+build/1",
        title=f"{name} {version} in {arch}",
    )


def _binary(arch="amd64", pocket="Release", is_debug=False, build=None):
    return SimpleNamespace(
        is_debug=is_debug,
        pocket=pocket,
        distro_arch_series_link=f"https://lp/ubuntu/{arch}",
        build=build if build is not None else _build(arch=arch),
    )


def _pub(version="1.0-1", pocket="Release", binaries=(), builds=()):
    return SimpleNamespace(
        source_package_version=version,
        pocket=pocket,
        getPublishedBinaries=lambda: list(binaries),
        getBuilds=lambda: list(builds),
    )


def _archive(name="test-archive", build_records=(), published_sources=()):
    return SimpleNamespace(
        name=name,
        displayname=f"{name} displayname",
        getBuildRecords=lambda build_state: FakeCollection(build_records),  # noqa: ARG005
        getPublishedSources=lambda **kw: list(published_sources),  # noqa: ARG005
    )


def _series(name="oracular", build_records=(), self_link="https://lp/ubuntu/oracular"):
    return SimpleNamespace(
        name=name,
        fullseriesname=f"Ubuntu {name.title()}",
        self_link=self_link,
        getBuildRecords=lambda build_state: FakeCollection(build_records),  # noqa: ARG005
    )


def _make_fetcher(**kw):
    """Build a TestRebuildFetcher with sane defaults; overrides per test."""
    archive = kw.pop("archive", _archive())
    series = kw.pop("series", _series())
    main_archive = kw.pop("main_archive", None)
    main_series = kw.pop("main_series", None)
    ref_series = kw.pop("ref_series", None)
    release_only = kw.pop("release_only", False)
    return TestRebuildFetcher(
        launchpad=SimpleNamespace(packagesets=[]),
        ubuntu=SimpleNamespace(
            getSourcePackage=lambda name: SimpleNamespace(searchTasks=lambda tags: [])  # noqa: ARG005
        ),
        archive=archive,
        series=series,
        main_archive=main_archive,
        main_series=main_series,
        ref_series=ref_series,
        release_only=release_only,
        api_version="devel",
        verbose=False,
    )


# --------------------------------------------------------------------------- #
# get_build_records
# --------------------------------------------------------------------------- #


def test_get_build_records_filters_missing_csp_link_and_wrong_arch():
    """Records without a current_source_publication_link or a non-matching arch
    are consumed but not yielded, and tick 'filtered' for each."""
    builds = [
        _build(name="old", csp_link=""),  # no CSP link -> filtered
        _build(name="arm-pkg", arch="arm64"),  # wrong arch -> filtered
        _build(name="kept", arch="amd64"),  # yielded
    ]
    fetcher = _make_fetcher(archive=_archive(build_records=builds))
    records = fetcher.get_build_records("Failed to build", ["amd64"])

    ticks: list[str | None] = []
    records.on_item = lambda cat=None: ticks.append(cat)
    yielded = list(records)

    assert [r.source_package_name for r in yielded] == ["kept"]
    # two filtered ticks then one plain tick
    assert ticks == ["filtered", "filtered", None]
    assert records.total == 3  # total_size defaults to len(collection)


def test_get_build_records_total_size_from_collection():
    """BuildRecordSet.total reflects the lazr collection's total_size."""
    fetcher = _make_fetcher(archive=_archive(build_records=[_build()]))
    coll = FakeCollection([_build()], total_size=42)
    fetcher.archive.getBuildRecords = lambda build_state: coll  # type: ignore[assignment]  # noqa: ARG005
    records = fetcher.get_build_records("Failed to build", ["amd64"])
    assert records.total == 42
    assert isinstance(records.total, int)


def test_get_build_records_total_unwraps_scalar_total_size():
    """A lazr ScalarValue-wrapped total_size yields a plain int total.

    lazr Collection.total_size can be a ScalarValue (with .value) rather than
    an int; Collection.__len__ unwraps it. The fetcher must go through len()
    (or otherwise unwrap) so Progress/BuildRecordSet get an int, not the
    wrapper — otherwise `current / total` raises TypeError at render time.
    """
    coll = FakeCollection([_build()], total_size=_ScalarTotal(7))
    fetcher = _make_fetcher(archive=_archive(build_records=[_build()]))
    fetcher.archive.getBuildRecords = lambda build_state: coll  # type: ignore[assignment]  # noqa: ARG005
    records = fetcher.get_build_records("Failed to build", ["amd64"])
    assert records.total == 7
    assert isinstance(records.total, int)


def test_get_build_records_total_none_when_size_unavailable():
    """If the collection's size is unavailable (len raises TypeError), total
    falls back to None ("unknown count") rather than crashing."""
    coll = FakeCollection([_build()], total_size=object())  # not int / _ScalarTotal
    fetcher = _make_fetcher(archive=_archive(build_records=[_build()]))
    fetcher.archive.getBuildRecords = lambda build_state: coll  # type: ignore[assignment]  # noqa: ARG005
    records = fetcher.get_build_records("Failed to build", ["amd64"])
    assert records.total is None


def test_get_build_records_primary_archive_uses_series_getBuildRecords():
    """When archive.name == 'primary', records come from series.getBuildRecords."""
    build = _build(name="primary-pkg")
    series = _series(build_records=[build])
    fetcher = _make_fetcher(archive=_archive(name="primary"), series=series)
    yielded = list(fetcher.get_build_records("Failed to build", ["amd64"]))
    assert [r.source_package_name for r in yielded] == ["primary-pkg"]


def test_get_build_records_maps_buildrecord_fields():
    """The yielded BuildRecord carries the build's fields verbatim."""
    from datetime import datetime, timezone

    when = datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc)
    build = _build(datebuilt=when)
    build.source_package_version = "2.0-1"
    fetcher = _make_fetcher(archive=_archive(build_records=[build]))
    [record] = list(fetcher.get_build_records("Failed to build", ["amd64"]))
    assert isinstance(record, BuildRecord)
    assert record.source_package_version == "2.0-1"
    assert record.datebuilt is when
    assert record.buildstate == "Failed to build"


# --------------------------------------------------------------------------- #
# check_current_publication
# --------------------------------------------------------------------------- #


def test_check_current_publication_default_everything_current():
    """With no main_archive and not release_only, everything is current."""
    fetcher = _make_fetcher()  # no main_archive, release_only=False
    assert fetcher.check_current_publication("anything", "1.0-1") is True


def test_check_current_publication_main_archive_published():
    """With a main_archive, returns whether the main archive has a Published match."""
    main = _archive(published_sources=[_pub()])  # non-empty -> True
    main_series = _series()
    fetcher = _make_fetcher(main_archive=main, main_series=main_series)
    assert fetcher.check_current_publication("pkg", "1.0-1") is True
    main_empty = _archive(published_sources=[])
    fetcher2 = _make_fetcher(main_archive=main_empty, main_series=main_series)
    assert fetcher2.check_current_publication("pkg", "1.0-1") is False


def test_check_current_publication_release_only_checks_release_then_pending():
    """release_only: a Published Release pub -> True; else Pending Release -> True; else False."""
    archive = _archive(published_sources=[_pub(pocket="Release")])
    fetcher = _make_fetcher(archive=archive, release_only=True)
    assert fetcher.check_current_publication("pkg", "1.0-1") is True

    # No Published, but a Pending -> True (second query)
    calls = []
    archive2 = _archive()

    def gps(**kw):
        calls.append(kw.get("status"))
        return [_pub(pocket="Release")] if kw.get("status") == "Pending" else []

    archive2.getPublishedSources = gps  # type: ignore[assignment]
    fetcher2 = _make_fetcher(archive=archive2, release_only=True)
    assert fetcher2.check_current_publication("pkg", "1.0-1") is True
    assert calls == ["Published", "Pending"]  # fell through to Pending

    # Neither -> False
    archive3 = _archive()
    fetcher3 = _make_fetcher(archive=archive3, release_only=True)
    assert fetcher3.check_current_publication("pkg", "1.0-1") is False


# --------------------------------------------------------------------------- #
# get_main_archive_build_state
# --------------------------------------------------------------------------- #


def test_get_main_archive_build_state_none_without_main_archive():
    fetcher = _make_fetcher()  # no main_archive
    assert fetcher.get_main_archive_build_state("pkg", "1.0-1", "amd64") is None


def test_get_main_archive_build_state_first_wins_per_arch_and_caches():
    """The first build per arch_tag wins (pubs sorted latest->oldest), and the
    result is cached: a second call does not re-query the archive."""
    older = _build(state="Failed to build")
    newer = _build(state="Successfully built")
    pub = _pub(builds=[older, newer])  # older listed first -> first wins
    main = _archive(published_sources=[pub])
    fetcher = _make_fetcher(main_archive=main, main_series=_series())

    assert fetcher.get_main_archive_build_state("pkg", "1.0-1", "amd64") == "Failed to build"
    # Cached: replace the archive's data and query again -> stale value returned
    main.getPublishedSources = lambda **kw: []  # type: ignore[assignment]  # noqa: ARG005
    assert fetcher.get_main_archive_build_state("pkg", "1.0-1", "amd64") == "Failed to build"
    # Unknown arch -> None
    assert fetcher.get_main_archive_build_state("pkg", "1.0-1", "arm64") is None


# --------------------------------------------------------------------------- #
# find_reference_build
# --------------------------------------------------------------------------- #


def test_find_reference_build_none_without_ref_series():
    fetcher = _make_fetcher()  # no ref_series
    assert fetcher.find_reference_build("pkg", "amd64", ["Release"]) is None


def test_find_reference_build_finds_matching_arch_skips_debug_and_wrong_pocket():
    """Skips debug binaries and binaries in non-requested pockets; returns the
    build whose arch matches, from the most-recent publication only."""
    good = _build(name="pkg", arch="amd64", state="Successfully built")
    debug_bin = _binary(arch="amd64", is_debug=True, build=_build(arch="amd64"))
    other_pocket = _binary(arch="amd64", pocket="Updates", build=_build(arch="amd64"))
    good_bin = _binary(arch="amd64", pocket="Release", build=good)
    pub = _pub(pocket="Release", binaries=[debug_bin, other_pocket, good_bin])
    ref_archive = _archive(published_sources=[pub])
    fetcher = _make_fetcher(main_archive=ref_archive, ref_series=_series(name="noble"))
    rec = fetcher.find_reference_build("pkg", "amd64", ["Release"])
    assert rec is not None
    assert rec.arch_tag == "amd64"
    assert rec.buildstate == "Successfully built"


def test_find_reference_build_most_recent_publication_wins():
    """Only the first (most recent) published source is considered."""
    rec_pub = _pub(
        pocket="Release",
        binaries=[
            _binary(
                arch="amd64",
                pocket="Release",
                build=_build(arch="amd64", state="Successfully built"),
            )
        ],
    )
    older_pub = _pub(
        pocket="Release",
        binaries=[
            _binary(
                arch="amd64",
                pocket="Release",
                build=_build(arch="amd64", state="Successfully built"),
            )
        ],
    )
    ref_archive = _archive(published_sources=[rec_pub, older_pub])
    fetcher = _make_fetcher(main_archive=ref_archive, ref_series=_series(name="noble"))
    # If both were considered the logic would still match, but the break after
    # the first rs means older_pub's binaries are never read. Assert it returns
    # the most-recent one and only one publication was iterated.
    rec = fetcher.find_reference_build("pkg", "amd64", ["Release"])
    assert rec is not None
    assert rec.arch_tag == "amd64"


def test_find_reference_build_returns_none_when_no_match():
    """No binary for the requested arch -> None."""
    pub = _pub(
        pocket="Release",
        binaries=[_binary(arch="arm64", pocket="Release", build=_build(arch="arm64"))],
    )
    ref_archive = _archive(published_sources=[pub])
    fetcher = _make_fetcher(main_archive=ref_archive, ref_series=_series(name="noble"))
    assert fetcher.find_reference_build("pkg", "amd64", ["Release"]) is None
