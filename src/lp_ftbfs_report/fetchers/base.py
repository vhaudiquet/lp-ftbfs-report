#!/usr/bin/python3

# Copyright © 2007-2010 Michael Bienia <geser@ubuntu.com>
# Authors:
# Michael Bienia <geser@ubuntu.com>
# Andrea Gasparini <gaspa@yattaweb.it>
# License:
# GPLv2 (or later), see /usr/share/common-licenses/GPL

"""Base fetcher interface for FTBFS report generator."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class BuildRecord:
    """Normalized build record from any source."""

    source_package_name: str
    source_package_version: str
    arch_tag: str
    buildstate: str  # "Failed to build", "Successfully built", etc.
    datebuilt: datetime | None
    current_source_publication_link: str
    build_log_url: str | None
    upload_log_url: str | None
    dependencies: str | None  # For dependency wait
    self_link: str  # For generating web URLs


@dataclass
class ArchiveInfo:
    """Archive metadata."""

    name: str
    displayname: str
    is_ppa: bool = False


@dataclass
class SeriesInfo:
    """Series metadata."""

    name: str
    fullseriesname: str
    self_link: str | None = None


class BaseFetcher(ABC):
    """Abstract base class for data fetchers.

    This defines the interface that all fetchers (TestRebuild, PPA, Dummy)
    must implement to provide build failure data.
    """

    @abstractmethod
    def get_archive_info(self) -> ArchiveInfo:
        """Get archive metadata.

        Returns:
            ArchiveInfo with name and display name
        """
        pass

    @abstractmethod
    def get_series_info(self) -> SeriesInfo:
        """Get series metadata.

        Returns:
            SeriesInfo with name and full name
        """
        pass

    @abstractmethod
    def get_build_records(
        self,
        state: str,
        arch_list: list[str],
        last_published: datetime | None = None,
    ) -> Iterator[BuildRecord]:
        """Get build records matching the given state and architectures.

        Args:
            state: Build state to filter by (e.g., "Failed to build")
            arch_list: List of architectures to include
            last_published: Optional timestamp to only get builds after this time

        Yields:
            BuildRecord objects
        """
        pass

    @abstractmethod
    def check_current_publication(
        self,
        source_name: str,
        version: str,
        pocket: str | None = None,
    ) -> bool:
        """Check if a source package version is currently published.

        Args:
            source_name: Source package name
            version: Package version
            pocket: Optional pocket to check ("Release", "Updates", etc.)

        Returns:
            True if the package is currently published
        """
        pass

    @abstractmethod
    def find_reference_build(
        self,
        source_name: str,
        arch: str,
        pockets: list[str],
    ) -> BuildRecord | None:
        """Find a successful build in reference series/pockets.

        Used to determine if a package has "never built before".

        Args:
            source_name: Source package name
            arch: Architecture to search for
            pockets: List of pockets to search ("Release", "Updates", etc.)

        Returns:
            BuildRecord if found, None otherwise
        """
        pass

    @abstractmethod
    def get_packagesets(self) -> dict[str, list[str]]:
        """Get package sets for this series.

        Returns:
            Dictionary mapping package set names to lists of source package names
        """
        pass

    @abstractmethod
    def get_teams(self) -> dict[str, list[str]]:
        """Get team assignments for packages.

        Returns:
            Dictionary mapping team names to lists of source package names
        """
        pass

    @abstractmethod
    def get_main_archive_build_state(
        self,
        source_name: str,
        version: str,
        arch: str,
    ) -> str | None:
        """Get build state from main archive for regression detection.

        Args:
            source_name: Source package name
            version: Package version
            arch: Architecture

        Returns:
            Build state string if found, None otherwise
        """
        pass

    @abstractmethod
    def load_launchpad_object(self, link: str) -> Any:
        """Load a Launchpad object by URL.

        This is used by models.py for lazy loading.

        Args:
            link: Launchpad API URL

        Returns:
            Launchpad object
        """
        pass

    @abstractmethod
    def search_bugs(self, source_name: str, tag: str) -> list[Any]:
        """Search for bugs tagged with a specific tag.

        Args:
            source_name: Source package name
            tag: Bug tag to search for (e.g., "ftbfs")

        Returns:
            List of bug objects
        """
        pass


class FetcherContext:
    """Context object passed to models during creation.

    This provides access to the fetcher and other shared state
    without tightly coupling models to a specific fetcher implementation.
    """

    def __init__(
        self,
        fetcher: BaseFetcher,
        launchpad: Any,
        ubuntu: Any,
        find_tagged_bugs: str | None,
        packagesets: dict[str, list[str]],
        packagesets_ftbfs: dict[str, list[Any]],
        teams: dict[str, list[str]],
        teams_ftbfs: dict[str, list[Any]],
        components: dict[str, list[Any]],
        api_version: str = "devel",
    ):
        """Initialize fetcher context.

        Args:
            fetcher: The active fetcher instance
            launchpad: Launchpad instance (for compatibility)
            ubuntu: Ubuntu distribution (for compatibility)
            find_tagged_bugs: Bug tag to search for
            packagesets: Package sets dictionary
            packagesets_ftbfs: FTBFS packages per package set
            teams: Teams dictionary
            teams_ftbfs: FTBFS packages per team
            components: Components dictionary
            api_version: API version string
        """
        self.fetcher = fetcher
        self.launchpad = launchpad
        self.ubuntu = ubuntu
        self.find_tagged_bugs = find_tagged_bugs
        self.packagesets = packagesets
        self.packagesets_ftbfs = packagesets_ftbfs
        self.teams = teams
        self.teams_ftbfs = teams_ftbfs
        self.components = components
        self.api_version = api_version


# Utility functions shared by fetchers


def translate_api_web(self_url: str, api_version: str = "devel") -> str:
    """Translate an API URL to a web URL.

    Args:
        self_url: Launchpad API URL
        api_version: API version string

    Returns:
        Web URL
    """
    if self_url is None:
        return ""
    else:
        return self_url.replace("api.", "").replace(f"{api_version}/", "")
