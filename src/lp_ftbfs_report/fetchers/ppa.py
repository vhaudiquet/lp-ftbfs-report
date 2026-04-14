#!/usr/bin/python3

# Copyright © 2007-2010 Michael Bienia <geser@ubuntu.com>
# Authors:
# Michael Bienia <geser@ubuntu.com>
# Andrea Gasparini <gaspa@yattaweb.it>
# License:
# GPLv2 (or later), see /usr/share/common-licenses/GPL

"""PPA fetcher for FTBFS report generator."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Any

from lp_ftbfs_report.fetchers.base import (
    ArchiveInfo,
    BaseFetcher,
    BuildRecord,
    SeriesInfo,
)


class PPAFetcher(BaseFetcher):
    """Fetcher for Personal Package Archives (PPAs).

    PPAs have a simpler structure than test rebuild archives:
    - No packagesets (PPA-specific)
    - No team mappings
    - Simplified regression detection
    - No updates archive integration
    """

    def __init__(
        self,
        launchpad: Any,
        ubuntu: Any,
        ppa_owner: str,
        ppa_name: str,
        series_name: str,
        api_version: str = "devel",
    ):
        """Initialize PPA fetcher.

        Args:
            launchpad: Launchpad instance
            ubuntu: Ubuntu distribution
            ppa_owner: PPA owner username
            ppa_name: PPA name
            series_name: Series name (e.g., "oracular")
            api_version: API version string
        """
        self.launchpad = launchpad
        self.ubuntu = ubuntu
        self.ppa_owner = ppa_owner
        self.ppa_name = ppa_name
        self.series_name = series_name
        self.api_version = api_version

        # Get PPA owner object
        self.owner = launchpad.people[ppa_owner]
        if not self.owner:
            raise ValueError(f"PPA owner '{ppa_owner}' not found")

        # Get PPA archive
        self.ppa = self.owner.getPPAByName(name=ppa_name)
        if not self.ppa:
            raise ValueError(f"PPA '{ppa_owner}/{ppa_name}' not found")

        # Get series
        self.series = ubuntu.getSeries(name_or_version=series_name)
        if not self.series:
            raise ValueError(f"Series '{series_name}' not found")

        # Cache
        self._packagesets: dict[str, list[str]] = {}
        self._teams: dict[str, list[str]] = {}

    def get_archive_info(self) -> ArchiveInfo:
        """Get archive metadata."""
        return ArchiveInfo(
            name=f"ppa:{self.ppa_owner}/{self.ppa_name}",
            displayname=self.ppa.displayname or f"{self.ppa_owner}'s {self.ppa_name} PPA",
            is_ppa=True,
        )

    def get_series_info(self) -> SeriesInfo:
        """Get series metadata."""
        return SeriesInfo(
            name=self.series.name,
            fullseriesname=self.series.fullseriesname,
            self_link=self.series.self_link,
        )

    def get_build_records(
        self,
        state: str,
        arch_list: list[str],
        last_published: datetime | None = None,
    ) -> Iterator[BuildRecord]:
        """Get build records matching the given state and architectures."""
        print(f"Processing PPA '{state}'")
        if last_published:
            last_published = last_published.replace(tzinfo=None)

        # PPAs use archive.getBuildRecords
        buildlist = self.ppa.getBuildRecords(build_state=state)

        for build in buildlist:
            if (
                last_published is not None
                and build.datebuilt is not None
                and last_published > build.datebuilt.replace(tzinfo=None)
            ):
                # Past the last known published build record
                break

            if not build.current_source_publication_link:
                # Build log for an older version
                continue

            if build.arch_tag not in arch_list:
                print(f"  Skipping {build.title}")
                continue

            print(f"  {build.datebuilt} {build.title}")

            # Convert to BuildRecord
            yield BuildRecord(
                source_package_name=build.source_package_name,
                source_package_version=getattr(build, "source_package_version", ""),
                arch_tag=build.arch_tag,
                buildstate=build.buildstate,
                datebuilt=build.datebuilt,
                current_source_publication_link=build.current_source_publication_link,
                build_log_url=build.build_log_url if hasattr(build, "build_log_url") else None,
                upload_log_url=(build.upload_log_url if hasattr(build, "upload_log_url") else None),
                dependencies=build.dependencies if hasattr(build, "dependencies") else None,
                self_link=build.self_link,
            )

    def check_current_publication(
        self,
        source_name: str,
        version: str,
        pocket: str | None = None,  # noqa: ARG002
    ) -> bool:
        """Check if a source package version is currently published.

        For PPAs, we simply check if it's published in any pocket.
        """
        publications = self.ppa.getPublishedSources(
            distro_series=self.series,
            exact_match=True,
            source_name=source_name,
            version=version,
            status="Published",
        )
        return len(publications[:1]) > 0

    def find_reference_build(
        self,
        source_name: str,
        arch: str,
        pockets: list[str],  # noqa: ARG002
    ) -> BuildRecord | None:
        """Find a successful build in the PPA.

        For PPAs, we look for any successful build of this package in this series.
        """
        print(f"    Find reference build in PPA: {source_name} / {arch}")

        # Get published sources for this package
        ref_sources = self.ppa.getPublishedSources(
            source_name=source_name,
            exact_match=True,
            distro_series=self.series,
            status="Published",
        )

        for rs in ref_sources:
            print(f"      v={rs.source_package_version}, {rs.pocket}")

            # Get builds for this source
            builds = rs.getBuilds()
            for build in builds:
                if build.arch_tag == arch and build.buildstate == "Successfully built":
                    print(f"        found: {build.source_package_name} {build.arch_tag}")
                    return BuildRecord(
                        source_package_name=build.source_package_name,
                        source_package_version=getattr(build, "source_package_version", ""),
                        arch_tag=build.arch_tag,
                        buildstate=build.buildstate,
                        datebuilt=build.datebuilt,
                        current_source_publication_link=getattr(
                            build, "current_source_publication_link", ""
                        ),
                        build_log_url=(
                            build.build_log_url if hasattr(build, "build_log_url") else None
                        ),
                        upload_log_url=(
                            build.upload_log_url if hasattr(build, "upload_log_url") else None
                        ),
                        dependencies=build.dependencies if hasattr(build, "dependencies") else None,
                        self_link=build.self_link,
                    )

        return None

    def get_packagesets(self) -> dict[str, list[str]]:
        """Get package sets for this series.

        PPAs don't have packagesets, return empty dict.
        """
        return self._packagesets

    def get_teams(self) -> dict[str, list[str]]:
        """Get team assignments for packages.

        PPAs don't have team mappings, return empty dict.
        """
        return self._teams

    def get_main_archive_build_state(
        self,
        source_name: str,  # noqa: ARG002
        version: str,  # noqa: ARG002
        arch: str,  # noqa: ARG002
    ) -> str | None:
        """Get build state from main archive for regression detection.

        PPAs don't do regression checking against main archive.
        """
        return None

    def load_launchpad_object(self, link: str) -> Any:
        """Load a Launchpad object by URL."""
        return self.launchpad.load(link)

    def search_bugs(self, source_name: str, tag: str) -> list[Any]:
        """Search for bugs tagged with a specific tag.

        Search in Ubuntu (not PPA-specific).
        """
        try:
            ts = self.ubuntu.getSourcePackage(name=source_name).searchTasks(tags=tag)
            return [t.bug for t in ts]
        except Exception as e:
            print(f"Warning: Could not search bugs for {source_name}: {e}")
            return []


def parse_ppa_spec(ppa_spec: str) -> tuple[str, str]:
    """Parse a PPA specification string.

    Args:
        ppa_spec: PPA specification in format "owner/ppaname" or "ppa:owner/ppaname"

    Returns:
        Tuple of (owner, ppaname)

    Raises:
        ValueError: If the PPA specification is invalid
    """
    # Remove "ppa:" prefix if present
    if ppa_spec.startswith("ppa:"):
        ppa_spec = ppa_spec[4:]

    # Split on "/"
    parts = ppa_spec.split("/")
    if len(parts) != 2:
        raise ValueError(
            f"Invalid PPA specification: '{ppa_spec}'. "
            "Expected format: 'owner/ppaname' or 'ppa:owner/ppaname'"
        )

    owner, ppaname = parts
    if not owner or not ppaname:
        raise ValueError(f"Invalid PPA specification: '{ppa_spec}'. Owner and name cannot be empty")

    return owner, ppaname
