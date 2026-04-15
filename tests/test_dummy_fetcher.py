"""Tests for DummyFetcher."""

from datetime import datetime

import pytest

from lp_ftbfs_report.fetchers import DummyFetcher


def test_dummy_fetcher_initialization(sample_fixture_path):
    """Test DummyFetcher can load fixture data."""
    fetcher = DummyFetcher(sample_fixture_path)
    assert fetcher is not None
    assert fetcher.data is not None


def test_get_archive_info(sample_fixture_path):
    """Test fetcher returns correct archive info."""
    fetcher = DummyFetcher(sample_fixture_path)
    archive_info = fetcher.get_archive_info()

    assert archive_info.name == "test-archive"
    assert archive_info.displayname == "Test Archive for Testing"
    assert archive_info.is_ppa is False


def test_get_series_info(sample_fixture_path):
    """Test fetcher returns correct series info."""
    fetcher = DummyFetcher(sample_fixture_path)
    series_info = fetcher.get_series_info()

    assert series_info.name == "oracular"
    assert series_info.fullseriesname == "Ubuntu Oracular"


def test_get_build_records_ftbfs(sample_fixture_path):
    """Test fetching build records for FTBFS."""
    fetcher = DummyFetcher(sample_fixture_path)
    builds = list(fetcher.get_build_records("Failed to build", ["amd64"]))

    # Should get 2 builds: example-pkg and always-fail-pkg on amd64
    assert len(builds) == 2
    assert all(b.buildstate == "Failed to build" for b in builds)
    assert all(b.arch_tag == "amd64" for b in builds)


def test_get_build_records_depwait(sample_fixture_path):
    """Test fetching dependency wait builds."""
    fetcher = DummyFetcher(sample_fixture_path)
    builds = list(fetcher.get_build_records("Dependency wait", ["amd64"]))

    assert len(builds) == 1
    assert builds[0].source_package_name == "depwait-pkg"
    assert builds[0].dependencies == "libfoo-dev (>= 1.0)"


def test_get_build_records_multiple_arches(sample_fixture_path):
    """Test fetching builds for multiple architectures."""
    fetcher = DummyFetcher(sample_fixture_path)
    builds = list(fetcher.get_build_records("Failed to build", ["amd64", "arm64"]))

    # Should get 3 builds: example-pkg on amd64 and arm64, always-fail-pkg on amd64
    assert len(builds) == 3


def test_check_current_publication(sample_fixture_path):
    """Test checking if package is currently published."""
    fetcher = DummyFetcher(sample_fixture_path)

    # All test packages are current
    assert fetcher.check_current_publication("example-pkg", "1.0-1")
    assert fetcher.check_current_publication("depwait-pkg", "2.0-1ubuntu1")


def test_find_reference_build(sample_fixture_path):
    """Test finding reference builds."""
    fetcher = DummyFetcher(sample_fixture_path)

    # example-pkg has a successful build in reference data
    ref_build = fetcher.find_reference_build("example-pkg", "amd64", ["Release"])
    assert ref_build is not None
    assert ref_build.buildstate == "Successfully built"

    # always-fail-pkg has no reference build
    ref_build = fetcher.find_reference_build("always-fail-pkg", "amd64", ["Release"])
    assert ref_build is None


def test_get_packagesets(sample_fixture_path):
    """Test fetching package sets."""
    fetcher = DummyFetcher(sample_fixture_path)
    packagesets = fetcher.get_packagesets()

    assert "server" in packagesets
    assert "depwait-pkg" in packagesets["server"]


def test_get_teams(sample_fixture_path):
    """Test fetching team mappings."""
    fetcher = DummyFetcher(sample_fixture_path)
    teams = fetcher.get_teams()

    assert "server-team" in teams
    assert "depwait-pkg" in teams["server-team"]


def test_search_bugs(sample_fixture_path):
    """Test searching for bugs."""
    fetcher = DummyFetcher(sample_fixture_path)

    # example-pkg has bugs tagged with ftbfs
    tasks = fetcher.search_bugs("example-pkg", "ftbfs")
    assert len(tasks) == 1
    assert tasks[0].bug.id == 123456

    # Package with no bugs
    tasks = fetcher.search_bugs("nonexistent-pkg", "ftbfs")
    assert len(tasks) == 0


def test_last_published_filter(sample_fixture_path):
    """Test filtering by last_published timestamp."""
    fetcher = DummyFetcher(sample_fixture_path)

    # Filter with a date that should exclude all builds
    last_published = datetime(2026, 5, 1)
    builds = list(fetcher.get_build_records("Failed to build", ["amd64"], last_published))
    assert len(builds) == 0

    # Filter with a date that should include builds
    last_published = datetime(2026, 3, 1)
    builds = list(fetcher.get_build_records("Failed to build", ["amd64"], last_published))
    assert len(builds) == 2


def test_invalid_fixture_path():
    """Test handling of invalid fixture path."""
    with pytest.raises(FileNotFoundError):
        DummyFetcher("nonexistent.json")


def test_missing_required_fields(tmp_path):
    """Test handling of fixture with missing required fields."""
    import json

    # Create invalid fixture missing 'builds' field
    invalid_fixture = tmp_path / "invalid.json"
    with open(invalid_fixture, "w") as f:
        json.dump({"archive": {"name": "test"}, "series": {"name": "test"}}, f)

    with pytest.raises(ValueError, match="missing 'builds' field"):
        DummyFetcher(str(invalid_fixture))
