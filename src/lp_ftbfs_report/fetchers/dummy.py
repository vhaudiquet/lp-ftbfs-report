#!/usr/bin/python3

# Copyright © 2007-2010 Michael Bienia <geser@ubuntu.com>
# Authors:
# Michael Bienia <geser@ubuntu.com>
# Andrea Gasparini <gaspa@yattaweb.it>
# License:
# GPLv2 (or later), see /usr/share/common-licenses/GPL

"""Dummy fetcher for testing with static JSON fixtures."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from lp_ftbfs_report.fetchers.base import (
    ArchiveInfo,
    BaseFetcher,
    BuildRecord,
    SeriesInfo,
)


class DummyFetcher(BaseFetcher):
    """Fetcher that loads data from JSON fixtures for testing.

    This fetcher is used for testing the report generation pipeline
    without requiring access to Launchpad. All data is loaded from
    a JSON fixture file.
    """

    def __init__(self, fixture_path: str | Path, api_version: str = "devel"):
        """Initialize dummy fetcher.

        Args:
            fixture_path: Path to JSON fixture file
            api_version: API version string (for compatibility)
        """
        self.fixture_path = Path(fixture_path)
        self.api_version = api_version

        # Load fixture data
        with open(self.fixture_path) as f:
            self.data = json.load(f)

        # Validate required fields
        if "archive" not in self.data:
            raise ValueError("Fixture missing 'archive' field")
        if "series" not in self.data:
            raise ValueError("Fixture missing 'series' field")
        if "builds" not in self.data:
            raise ValueError("Fixture missing 'builds' field")

        # Optional fields with defaults
        self.publications = self.data.get("publications", {})
        self.packagesets = self.data.get("packagesets", {})
        self.teams_data = self.data.get("teams", {})
        self.bugs_data = self.data.get("bugs", {})
        self.reference_builds_data = self.data.get("reference_builds", {})

    def get_archive_info(self) -> ArchiveInfo:
        """Get archive metadata."""
        return ArchiveInfo(
            name=self.data["archive"]["name"],
            displayname=self.data["archive"]["displayname"],
            is_ppa=self.data["archive"].get("is_ppa", False),
        )

    def get_series_info(self) -> SeriesInfo:
        """Get series metadata."""
        return SeriesInfo(
            name=self.data["series"]["name"],
            fullseriesname=self.data["series"]["fullseriesname"],
            self_link=self.data["series"].get("self_link"),
        )

    def get_build_records(
        self,
        state: str,
        arch_list: list[str],
        last_published: datetime | None = None,
    ) -> Iterator[BuildRecord]:
        """Get build records matching the given state and architectures."""
        print(f"Processing dummy data '{state}'")

        for build_data in self.data["builds"]:
            # Filter by build state
            if build_data["buildstate"] != state:
                continue

            # Filter by architecture
            if build_data["arch_tag"] not in arch_list:
                continue

            # Parse datebuilt
            datebuilt = None
            if build_data.get("datebuilt"):
                datebuilt = datetime.fromisoformat(build_data["datebuilt"].replace("+00:00", ""))

            # Filter by last_published timestamp
            if last_published and datebuilt:
                # Ensure both timestamps are timezone-naive for comparison
                last_pub_naive = (
                    last_published.replace(tzinfo=None) if last_published.tzinfo else last_published
                )
                if last_pub_naive > datebuilt:
                    continue

            print(f"  {datebuilt} {build_data['source_package_name']} {build_data['arch_tag']}")

            yield BuildRecord(
                source_package_name=build_data["source_package_name"],
                source_package_version=build_data.get("source_package_version", ""),
                arch_tag=build_data["arch_tag"],
                buildstate=build_data["buildstate"],
                datebuilt=datebuilt,
                current_source_publication_link=build_data["current_source_publication_link"],
                build_log_url=build_data.get("build_log_url"),
                upload_log_url=build_data.get("upload_log_url"),
                dependencies=build_data.get("dependencies"),
                self_link=build_data.get("self_link", ""),
            )

    def check_current_publication(
        self,
        source_name: str,
        version: str,
        pocket: str | None = None,  # noqa: ARG002
    ) -> bool:
        """Check if a source package version is currently published."""
        # Check if any build for this source/version is marked as current
        for build_data in self.data["builds"]:
            if (
                build_data["source_package_name"] == source_name
                and build_data.get("source_package_version") == version
            ):
                return build_data.get("is_current", True)
        return True

    def find_reference_build(
        self,
        source_name: str,
        arch: str,
        pockets: list[str],  # noqa: ARG002
    ) -> BuildRecord | None:
        """Find a successful build in reference data.

        Args:
            source_name: Source package name
            arch: Architecture
            pockets: List of pockets (ignored in dummy data)

        Returns:
            BuildRecord if found, None otherwise
        """
        if source_name not in self.reference_builds_data:
            return None

        if arch not in self.reference_builds_data[source_name]:
            return None

        ref_data = self.reference_builds_data[source_name][arch]
        datebuilt = None
        if ref_data.get("datebuilt"):
            datebuilt = datetime.fromisoformat(ref_data["datebuilt"].replace("+00:00", ""))

        return BuildRecord(
            source_package_name=source_name,
            source_package_version=ref_data.get("version", "unknown"),
            arch_tag=arch,
            buildstate=ref_data.get("buildstate", "Successfully built"),
            datebuilt=datebuilt,
            current_source_publication_link="",
            build_log_url=None,
            upload_log_url=None,
            dependencies=None,
            self_link="",
        )

    def get_packagesets(self) -> dict[str, list[str]]:
        """Get package sets for this series."""
        return self.packagesets

    def get_teams(self) -> dict[str, list[str]]:
        """Get team assignments for packages."""
        return self.teams_data

    def get_main_archive_build_state(
        self,
        source_name: str,  # noqa: ARG002
        version: str,  # noqa: ARG002
        arch: str,  # noqa: ARG002
    ) -> str | None:
        """Get build state from main archive for regression detection.

        Dummy fetcher doesn't support regression checking.
        """
        return None

    def load_launchpad_object(self, link: str) -> Any:
        """Load a Launchpad object by URL.

        Returns a mock object with publication data.
        """
        if link in self.publications:
            pub_data = self.publications[link]

            class MockPublication:
                def __init__(self, data):
                    self.source_package_name = data["source_package_name"]
                    self.source_package_version = data.get("source_package_version", "")
                    self.component_name = data.get("component_name", "universe")
                    self.pocket = data.get("pocket", "Release")
                    self.package_creator_link = data.get("package_creator_link", "")

            return MockPublication(pub_data)

        # Return a generic mock object
        class MockObject:
            def __init__(self):
                self.display_name = "Mock Object"
                self.name = "mock"

        return MockObject()

    def search_bugs(self, source_name: str, tag: str) -> list[Any]:
        """Search for bugs tagged with a specific tag."""
        if source_name not in self.bugs_data:
            return []

        class MockBug:
            def __init__(self, bug_data):
                self.id = bug_data["id"]
                self.title = bug_data["title"]
                self.tags = bug_data.get("tags", [])

        class MockTask:
            def __init__(self, bug_data):
                self.bug = MockBug(bug_data)

        bugs = []
        for bug_data in self.bugs_data[source_name]:
            if tag in bug_data.get("tags", []):
                bugs.append(MockTask(bug_data))
        return bugs

    def create_mock_series(self) -> Any:
        """Create a mock series object for compatibility with build_status.py.

        Returns:
            A mock series object with the necessary attributes and methods.
        """
        series_info = self.get_series_info()

        class MockSeries:
            def __init__(self, info):
                self.name = info.name
                self.fullseriesname = info.fullseriesname
                self.self_link = info.self_link

            def getDistroArchSeries(self, archtag):  # noqa: ARG002
                class MockDAS:
                    official = True

                return MockDAS()

        return MockSeries(series_info)

    def create_mock_archive(self) -> Any:
        """Create a mock archive object for HTML/CSV generation.

        Returns:
            A mock archive object with name and displayname attributes.
        """
        archive_info = self.get_archive_info()

        class MockArchive:
            def __init__(self, info):
                self.name = info.name
                self.displayname = info.displayname

        return MockArchive(archive_info)

    def create_mock_launchpad(self) -> Any:
        """Create a mock Launchpad object that intercepts API calls.

        Returns:
            A mock Launchpad object that uses the fetcher's data instead of real API calls.
        """
        fetcher_ref = self

        class MockLaunchpad:
            def __init__(self):
                self.fetcher = fetcher_ref
                # Mock distributions for compatibility
                self.distributions = {"ubuntu": self}
                # Mock packagesets (empty list, will use fetcher.get_packagesets instead)
                self.packagesets = []

            def load(self, url):
                return self.fetcher.load_launchpad_object(url)

            def search_tasks(self, *args, **kwargs):  # noqa: ARG002
                # For bug searches
                tags = kwargs.get("tags", [])
                source_name = kwargs.get("source_package_name", "")
                if tags and source_name:
                    return self.fetcher.search_bugs(source_name, tags[0])
                return []

            def getSourcePackage(self, name):  # noqa: ARG002
                # Return a mock source package for bug searches
                parent = self

                class MockSourcePackage:
                    def searchTasks(self, tags):
                        return parent.fetcher.search_bugs(name, tags)

                return MockSourcePackage()

        return MockLaunchpad()
