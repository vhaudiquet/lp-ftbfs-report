"""Tests for report_data serialization/deserialization round-trip.

These tests exercise the two-step pipeline's bridge layer
(:mod:`lp_ftbfs_report.report_data`):

  Step 1 – Fetch:   network / Launchpad API  →  JSON file   (build_status.py)
  Step 2 – Render:  JSON file                →  HTML + CSV  (render.py)

The core invariant under test is **round-trip fidelity**: rendering HTML/CSV
from deserialized proxy objects must produce byte-identical output to
rendering directly from the original in-memory model objects.
"""

from __future__ import annotations

import pytest

from lp_ftbfs_report.csv_generator import generate_csvfile
from lp_ftbfs_report.data_fetcher import FetchContext, ReportAccumulators, fetch_pkg_list
from lp_ftbfs_report.fetchers import DummyFetcher
from lp_ftbfs_report.html_generator import generate_page
from lp_ftbfs_report.models import ModelCaches
from lp_ftbfs_report.report_data import deserialize_report, read_json, serialize_report, write_json

ARCHIVE_STATES = (
    "Failed to build",
    "Dependency wait",
    "Chroot problem",
    "Failed to upload",
    "Cancelled build",
)

ARCH_LIST = ["amd64", "arm64", "riscv64"]


def _build_report(fixture_path: str) -> tuple:
    """Run the dummy fetcher pipeline to produce in-memory SourcePackage objects.

    Mirrors the relevant section of ``build_status.main()``: set up a
    :class:`DummyFetcher`, accumulate packages into the component/packageset/
    team dicts, and return them alongside a ``meta`` dict suitable for
    :func:`serialize_report`.
    """
    fetcher = DummyFetcher(fixture_path)
    series = fetcher.create_mock_series()
    archive = fetcher.create_mock_archive()
    launchpad = fetcher.create_mock_launchpad()

    caches = ModelCaches()
    components: dict[str, list] = {
        "main": [],
        "restricted": [],
        "universe": [],
        "multiverse": [],
    }
    packagesets = fetcher.get_packagesets()
    packagesets_ftbfs: dict[str, list] = {ps: [] for ps in packagesets}
    teams = fetcher.get_teams()
    teams_ftbfs: dict[str, list] = {team: [] for team in teams}

    ctx = FetchContext(
        launchpad=launchpad,
        ubuntu=launchpad,
        main_archive=None,
        ref_series=None,
        find_tagged_bugs="ftbfs",
        caches=caches,
    )
    accumulators = ReportAccumulators(
        components=components,
        packagesets=packagesets,
        packagesets_ftbfs=packagesets_ftbfs,
        teams=teams,
        teams_ftbfs=teams_ftbfs,
    )

    for i, state in enumerate(ARCHIVE_STATES, start=1):
        fetch_pkg_list(
            state=state,
            arch_list=ARCH_LIST,
            fetcher=fetcher,
            accumulators=accumulators,
            ctx=ctx,
            state_index=i,
            state_count=len(ARCHIVE_STATES),
        )

    meta = {
        "name": "test-report",
        "generated_started": "2026-01-01T00:00:00+00:00",
        "generated_finished": "2026-01-01T00:00:00+00:00",
        "archive": {"name": archive.name, "displayname": archive.displayname},
        "updates_archive": None,
        "main_archive": None,
        "series": {"name": series.name, "fullseriesname": series.fullseriesname},
        "arch_list": ARCH_LIST,
        "notice": None,
        "release_only": False,
        "ref_series": None,
    }

    return components, packagesets_ftbfs, teams_ftbfs, meta


# --------------------------------------------------------------------------- #
# serialize_report
# --------------------------------------------------------------------------- #


def test_serialize_report_structure(comprehensive_fixture_path):
    """serialize_report produces a dict with the expected top-level keys."""
    components, packagesets_ftbfs, teams_ftbfs, meta = _build_report(comprehensive_fixture_path)

    data = serialize_report(components, packagesets_ftbfs, teams_ftbfs, meta)

    assert set(data) == {"meta", "packages", "components", "packagesets_ftbfs", "teams_ftbfs"}
    assert data["meta"] == meta


def test_serialize_report_deduplicates_packages(comprehensive_fixture_path):
    """Packages appearing in multiple groupings are flattened into one 'packages' entry."""
    components, packagesets_ftbfs, teams_ftbfs, meta = _build_report(comprehensive_fixture_path)

    data = serialize_report(components, packagesets_ftbfs, teams_ftbfs, meta)

    # Every name referenced from components / packagesets / teams exists in the
    # flat packages dict — no dangling references.
    flat = set(data["packages"])
    for names in data["components"].values():
        assert set(names).issubset(flat)
    for names in data["packagesets_ftbfs"].values():
        assert set(names).issubset(flat)
    for names in data["teams_ftbfs"].values():
        assert set(names).issubset(flat)


def test_serialize_report_is_json_serializable(comprehensive_fixture_path):
    """The serialized dict can be passed through json.dumps without error."""
    components, packagesets_ftbfs, teams_ftbfs, meta = _build_report(comprehensive_fixture_path)

    data = serialize_report(components, packagesets_ftbfs, teams_ftbfs, meta)

    import json

    json.dumps(data)  # raises TypeError if not serializable


# --------------------------------------------------------------------------- #
# deserialize_report
# --------------------------------------------------------------------------- #


def test_deserialize_report_proxy_attributes(comprehensive_fixture_path):
    """Deserialized proxy objects expose the same data as the originals."""
    components, packagesets_ftbfs, teams_ftbfs, meta = _build_report(comprehensive_fixture_path)
    data = serialize_report(components, packagesets_ftbfs, teams_ftbfs, meta)

    d_components, _, _, render_kwargs = deserialize_report(data)

    # Name round-trips through meta.
    assert render_kwargs["name"] == meta["name"]

    # Each proxy package's name matches its key in the flat packages dict.
    for pkg_name, pkg_data in data["packages"].items():
        proxies = [p for comp in d_components.values() for p in comp if p.name == pkg_name]
        if not proxies:
            continue
        proxy = proxies[0]
        assert proxy.url == pkg_data["url"]
        assert proxy.packagesets == set(pkg_data["packagesets"])
        assert proxy.teams == set(pkg_data["teams"])
        assert len(proxy.versions) == len(pkg_data["versions"])


def test_deserialize_report_renders_same_html(comprehensive_fixture_path, tmp_path):
    """HTML rendered from deserialized proxies is byte-identical to direct render."""
    components, packagesets_ftbfs, teams_ftbfs, meta = _build_report(comprehensive_fixture_path)

    # Direct render from in-memory model objects.
    direct_dir = tmp_path / "direct"
    direct_dir.mkdir()
    generate_page(
        meta["name"],
        _MockArchive(meta["archive"]),
        None,
        _MockSeries(meta["series"]),
        None,
        components,
        packagesets_ftbfs,
        teams_ftbfs,
        arch_list=meta["arch_list"],
        output_dir=str(direct_dir),
        generated_started=meta["generated_started"],
        generated_finished=meta["generated_finished"],
        lastupdate="2026-01-01 00:00:00 +0000",
    )

    # Decoupled render: serialize → JSON → deserialize → render.
    data = serialize_report(components, packagesets_ftbfs, teams_ftbfs, meta)
    json_path = tmp_path / "report.json"
    write_json(data, str(json_path))
    d_components, d_ps, d_teams, render_kwargs = deserialize_report(read_json(str(json_path)))
    render_kwargs.pop("name")
    decoupled_dir = tmp_path / "decoupled"
    decoupled_dir.mkdir()
    generate_page(
        meta["name"],
        render_kwargs.pop("archive"),
        render_kwargs.pop("updates_archive"),
        render_kwargs.pop("series"),
        render_kwargs.pop("main_archive"),
        d_components,
        d_ps,
        d_teams,
        arch_list=render_kwargs.pop("arch_list"),
        output_dir=str(decoupled_dir),
        lastupdate="2026-01-01 00:00:00 +0000",
        **render_kwargs,
    )

    direct_html = (direct_dir / f"{meta['name']}.html").read_bytes()
    decoupled_html = (decoupled_dir / f"{meta['name']}.html").read_bytes()
    assert direct_html == decoupled_html


def test_deserialize_report_renders_same_csv(comprehensive_fixture_path, tmp_path):
    """CSV rendered from deserialized proxies is byte-identical to direct render."""
    components, packagesets_ftbfs, teams_ftbfs, meta = _build_report(comprehensive_fixture_path)

    direct_dir = tmp_path / "direct"
    direct_dir.mkdir()
    generate_csvfile(meta["name"], components, output_dir=str(direct_dir))

    data = serialize_report(components, packagesets_ftbfs, teams_ftbfs, meta)
    d_components, _, _, _ = deserialize_report(data)
    decoupled_dir = tmp_path / "decoupled"
    decoupled_dir.mkdir()
    generate_csvfile(meta["name"], d_components, output_dir=str(decoupled_dir))

    direct_csv = (direct_dir / f"{meta['name']}.csv").read_bytes()
    decoupled_csv = (decoupled_dir / f"{meta['name']}.csv").read_bytes()
    assert direct_csv == decoupled_csv


# --------------------------------------------------------------------------- #
# write_json / read_json
# --------------------------------------------------------------------------- #


def test_write_json_read_json_round_trip(tmp_path):
    """write_json + read_json preserves the data dict exactly."""
    data = {
        "meta": {"name": "test", "generated": ""},
        "packages": {},
        "components": {"main": []},
        "packagesets_ftbfs": {},
        "teams_ftbfs": {},
    }
    path = tmp_path / "report.json"
    write_json(data, str(path))
    assert read_json(str(path)) == data


def test_write_json_is_atomic(tmp_path):
    """write_json writes to a temp file then renames — the final path appears atomically."""
    data = {"meta": {}, "packages": {}}
    path = tmp_path / "report.json"
    write_json(data, str(path))
    assert path.exists()
    # No .new leftover from the atomic write.
    assert not (tmp_path / "report.json.new").exists()


# --------------------------------------------------------------------------- #
# Edge cases
# --------------------------------------------------------------------------- #


def test_deserialize_empty_report():
    """A report with no packages deserializes to empty containers."""
    data = {
        "meta": {
            "name": "empty",
            "generated_started": None,
            "generated_finished": None,
            "archive": {"name": "a", "displayname": "A"},
            "updates_archive": None,
            "main_archive": None,
            "series": {"name": "s", "fullseriesname": "S"},
            "notice": None,
            "release_only": False,
            "ref_series": None,
        },
        "packages": {},
        "components": {"main": [], "restricted": [], "universe": [], "multiverse": []},
        "packagesets_ftbfs": {},
        "teams_ftbfs": {},
    }
    components, packagesets_ftbfs, teams_ftbfs, render_kwargs = deserialize_report(data)

    assert all(v == [] for v in components.values())
    assert packagesets_ftbfs == {}
    assert teams_ftbfs == {}
    assert render_kwargs["name"] == "empty"


class _MockArchive:
    def __init__(self, data):
        self.name = data["name"]
        self.displayname = data["displayname"]


class _MockSeries:
    def __init__(self, data):
        self.name = data["name"]
        self.fullseriesname = data["fullseriesname"]


@pytest.fixture
def comprehensive_fixture_path(fixture_dir):
    """Return path to the comprehensive fixture file."""
    return str(fixture_dir / "comprehensive.json")
