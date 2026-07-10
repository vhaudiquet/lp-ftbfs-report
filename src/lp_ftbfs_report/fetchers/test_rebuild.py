#!/usr/bin/python3

# Copyright © 2007-2010 Michael Bienia <geser@ubuntu.com>
# Authors:
# Michael Bienia <geser@ubuntu.com>
# Andrea Gasparini <gaspa@yattaweb.it>
# License:
# GPLv2 (or later), see /usr/share/common-licenses/GPL

"""Test rebuild archive fetcher for FTBFS report generator."""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator
from typing import Any

import requests

from lp_ftbfs_report.fetchers.base import (
    ArchiveInfo,
    BaseFetcher,
    BuildRecord,
    BuildRecordSet,
    SeriesInfo,
)


class TestRebuildFetcher(BaseFetcher):
    """Fetcher for Ubuntu test rebuild archives.

    This implementation works with both primary archives and test rebuild
    archives, supporting all advanced features like regression detection,
    reference series comparison, and updates archive integration.
    """

    __test__ = False  # not a pytest test class despite the Test* name

    def __init__(
        self,
        launchpad: Any,
        ubuntu: Any,
        archive: Any,
        series: Any,
        main_archive: Any | None = None,
        main_series: Any | None = None,
        updates_archive: Any | None = None,
        ref_series: Any | None = None,
        release_only: bool = False,
        regressions_only: bool = False,
        api_version: str = "devel",
        verbose: bool = False,
    ):
        """Initialize test rebuild fetcher.

        Args:
            launchpad: Launchpad instance
            ubuntu: Ubuntu distribution
            archive: Target archive to analyze
            series: Target series
            main_archive: Optional main archive for regression detection
            main_series: Optional main series for regression detection
            updates_archive: Optional updates archive
            ref_series: Optional reference series for "never built" detection
            release_only: Only include release pocket packages
            regressions_only: Only report regressions
            api_version: API version string
        """
        self.launchpad = launchpad
        self.ubuntu = ubuntu
        self.archive = archive
        self.series = series
        self.main_archive = main_archive
        self.main_series = main_series
        self.updates_archive = updates_archive
        self.ref_series = ref_series
        self.release_only = release_only
        self.regressions_only = regressions_only
        self.api_version = api_version
        self.verbose = verbose

        # Caches
        self.update_builds: dict[tuple[str, str], Any] = {}
        # Successful builds found in the reference series, keyed by
        # (source_name, ref_series_name, pocket, arch) -> Launchpad build object.
        self._reference_build_cache: dict[tuple[str, str, str, str], Any] = {}
        # Build states from the main archive for regression detection, keyed by
        # (source_name, version) -> {arch_tag: buildstate}.
        self._main_build_state_cache: dict[tuple[str, str], dict[str, str]] = {}
        self._packagesets: dict[str, list[str]] | None = None
        self._teams: dict[str, list[str]] | None = None

    def get_archive_info(self) -> ArchiveInfo:
        """Get archive metadata."""
        return ArchiveInfo(
            name=self.archive.name,
            displayname=self.archive.displayname,
            is_ppa=False,
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
    ) -> BuildRecordSet:
        """Get build records matching the given state and architectures."""
        # XXX wgrant 2009-09-19: This is an awful hack. We should really
        # just let IArchive.getBuildRecords take a series argument.
        if self.archive.name == "primary":
            buildlist = self.series.getBuildRecords(build_state=state)
        else:
            buildlist = self.archive.getBuildRecords(build_state=state)

        # lazr.restfulclient Collections expose total_size (the count from the
        # LP API total_size link relation) but not __len__, so use it directly
        # instead of the old len() + except-Exception fallback that always
        # yielded None ("unknown count").
        total = getattr(buildlist, "total_size", None)

        arch_set = set(arch_list)
        verbose = self.verbose

        def factory(
            on_item: Callable[[str | None], None] | None,
        ) -> Iterator[BuildRecord]:
            for build in buildlist:
                if not build.current_source_publication_link:
                    # Build log for an older version
                    if on_item:
                        on_item("filtered")
                    continue

                if build.arch_tag not in arch_set:
                    if verbose:
                        print(f"  Skipping {build.title}", file=sys.stderr)
                    if on_item:
                        on_item("filtered")
                    continue

                if verbose:
                    print(f"  {build.datebuilt} {build.title}", file=sys.stderr)

                if on_item:
                    on_item(None)

                yield BuildRecord(
                    source_package_name=build.source_package_name,
                    source_package_version=getattr(build, "source_package_version", ""),
                    arch_tag=build.arch_tag,
                    buildstate=build.buildstate,
                    datebuilt=build.datebuilt,
                    current_source_publication_link=build.current_source_publication_link,
                    build_log_url=build.build_log_url,
                    upload_log_url=build.upload_log_url,
                    dependencies=build.dependencies,
                    self_link=build.self_link,
                )

        return BuildRecordSet(factory, total=total)

    def check_current_publication(
        self,
        source_name: str,
        version: str,
        pocket: str | None = None,  # noqa: ARG002
    ) -> bool:
        """Check if a source package version is currently published."""
        if self.main_archive:
            # Check main archive
            main_publications = self.main_archive.getPublishedSources(
                distro_series=self.main_series,
                exact_match=True,
                source_name=source_name,
                version=version,
                status="Published",
            )
            return len(main_publications[:1]) > 0
        elif self.release_only:
            # Check release pocket only
            release_publications = self.archive.getPublishedSources(
                distro_series=self.series,
                pocket="Release",
                exact_match=True,
                source_name=source_name,
                version=version,
                status="Published",
            )
            current = len(release_publications[:1]) > 0
            if not current:
                # Also check Pending
                release_publications = self.archive.getPublishedSources(
                    distro_series=self.series,
                    pocket="Release",
                    exact_match=True,
                    source_name=source_name,
                    version=version,
                    status="Pending",
                )
                current = len(release_publications[:1]) > 0
            return current
        else:
            # Everything is current
            return True

    def find_reference_build(
        self,
        source_name: str,
        arch: str,
        pockets: list[str],
    ) -> BuildRecord | None:
        """Find a successful build in reference series/pockets."""
        if not self.ref_series:
            return None

        if self.verbose:
            print(
                f"    Find reference build: {source_name} / {arch} / {pockets} / "
                f"{self.ref_series.name}",
                file=sys.stderr,
            )

        # Check cache first. Key by the *reference* series: the search is
        # performed against self.ref_series, so caching under the report
        # series would silently return the wrong series' build if a fetcher
        # ever served more than one report/ref-series pair.
        for pocket in pockets:
            br = self._reference_build_cache.get((source_name, self.ref_series.name, pocket, arch))
            if br:
                if self.verbose:
                    print(f"        cache: {br.source_package_name} {br.arch_tag}", file=sys.stderr)
                return self._build_to_record(br)

        # Determine which archive to search
        reference_archive = self.main_archive or self.archive

        # Get published sources
        if len(pockets) == 1:
            ref_sources = reference_archive.getPublishedSources(
                source_name=source_name,
                exact_match=True,
                distro_series=self.ref_series,
                status="Published",
                pocket=pockets[0],
            )
        else:
            ref_sources = reference_archive.getPublishedSources(
                source_name=source_name,
                exact_match=True,
                distro_series=self.ref_series,
                status="Published",
            )

        found = None
        for rs in ref_sources:
            if rs.pocket not in pockets:
                continue
            if self.verbose:
                print(f"      v={rs.source_package_version}, {rs.pocket}", file=sys.stderr)

            # Get binaries to find successful builds
            binaries = rs.getPublishedBinaries()
            for b in binaries:
                if b.is_debug:
                    continue
                if b.pocket not in pockets:
                    continue

                b_arch = b.distro_arch_series_link.split("/")[-1]

                # Get the build
                br = self._reference_build_cache.get(
                    (source_name, self.ref_series.name, b.pocket, b_arch)
                )
                if not br:
                    br = b.build

                # Cache for any architecture
                self._reference_build_cache[
                    (source_name, self.ref_series.name, b.pocket, b_arch)
                ] = br

                if arch == br.arch_tag:
                    found = br

            # Only interested in most recent
            break

        if found:
            if self.verbose:
                print(
                    f"        found: {found.source_package_name} {found.arch_tag}", file=sys.stderr
                )
            return self._build_to_record(found)
        return None

    def get_packagesets(self) -> dict[str, list[str]]:
        """Get package sets for this series."""
        if self._packagesets is None:
            self._packagesets = {}
            for ps in self.launchpad.packagesets:
                if ps.distroseries_link == self.series.self_link:
                    self._packagesets[ps.name] = ps.getSourcesIncluded(direct_inclusion=False)
        return self._packagesets

    def get_teams(self) -> dict[str, list[str]]:
        """Get team assignments for packages."""
        if self._teams is None:
            try:
                response = requests.get(
                    "https://people.canonical.com/~ubuntu-archive/package-team-mapping.json",
                    timeout=30,
                )
                response.raise_for_status()
                self._teams = response.json()
            except (requests.RequestException, ValueError) as e:
                print(f"Warning: Could not fetch team mappings: {e}", file=sys.stderr)
                self._teams = {}
        return self._teams

    def get_main_archive_build_state(
        self,
        source_name: str,
        version: str,
        arch: str,
    ) -> str | None:
        """Get build state from main archive for regression detection."""
        if not self.main_archive:
            return None

        # Check cache - key by (source_name, version) tuple to avoid the
        # comma-join collision risk (a comma in a name or version would
        # otherwise map two distinct packages to the same cache entry).
        cache_key = (source_name, version)
        results = self._main_build_state_cache.get(cache_key)
        if results is None:
            # Fetch and cache
            results = {}
            sourcepubs = self.main_archive.getPublishedSources(
                exact_match=True, source_name=source_name, version=version
            )
            for pub in sourcepubs:
                for build in pub.getBuilds():
                    # First record wins (sorted latest to oldest)
                    if build.arch_tag not in results:
                        results[build.arch_tag] = build.buildstate
            self._main_build_state_cache[cache_key] = results

        return results.get(arch)

    def load_launchpad_object(self, link: str) -> Any:
        """Load a Launchpad object by URL."""
        return self.launchpad.load(link)

    def search_bugs(self, source_name: str, tag: str) -> list[Any]:
        """Search for bugs tagged with a specific tag."""
        ts = self.ubuntu.getSourcePackage(name=source_name).searchTasks(tags=tag)
        return [t.bug for t in ts]

    def check_update_archive_success(self, source_name: str, arch: str) -> bool:
        """Check if build succeeded in updates archive.

        Args:
            source_name: Source package name
            arch: Architecture

        Returns:
            True if build succeeded in updates archive
        """
        return (source_name, arch) in self.update_builds

    def record_update_build(self, source_name: str, arch: str, build: Any) -> None:
        """Record a successful build from updates archive.

        Args:
            source_name: Source package name
            arch: Architecture
            build: Build object
        """
        self.update_builds[(source_name, arch)] = build

    def _build_to_record(self, build: Any) -> BuildRecord:
        """Convert Launchpad build object to BuildRecord."""
        return BuildRecord(
            source_package_name=build.source_package_name,
            source_package_version=getattr(build, "source_package_version", ""),
            arch_tag=build.arch_tag,
            buildstate=build.buildstate,
            datebuilt=build.datebuilt,
            current_source_publication_link=getattr(build, "current_source_publication_link", ""),
            build_log_url=build.build_log_url,
            upload_log_url=build.upload_log_url,
            dependencies=build.dependencies,
            self_link=build.self_link,
        )
