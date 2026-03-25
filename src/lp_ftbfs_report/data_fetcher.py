#!/usr/bin/python3

# Copyright © 2007-2010 Michael Bienia <geser@ubuntu.com>
# Authors:
# Michael Bienia <geser@ubuntu.com>
# Andrea Gasparini <gaspa@yattaweb.it>
# License:
# GPLv2 (or later), see /usr/share/common-licenses/GPL

"""Data fetching functions for FTBFS report generator."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from lp_ftbfs_report.models import SPPH, MainArchiveBuilds, SourcePackage

# cache: (source_package_name, arch_tag) -> build
update_builds: dict[tuple[str, str], Any] = {}

# cache: (source_package_name, series, pocket, arch_tag) -> build
reference_builds: dict[tuple[str, str, str, str], Any] = {}


def fetch_pkg_list(
    archive: Any,
    series: Any,
    state: str,
    last_published: datetime | None,
    launchpad: Any,
    ubuntu: Any,
    find_tagged_bugs: str | None,
    packagesets: dict[str, list[str]],
    packagesets_ftbfs: dict[str, list[SourcePackage]],
    teams: dict[str, list[str]],
    teams_ftbfs: dict[str, list[SourcePackage]],
    components: dict[str, list[SourcePackage]],
    arch_list: list[str] | None = None,
    main_archive: Any = None,
    main_series: Any = None,
    release_only: bool = False,
    is_updates_archive: bool = False,
    regressions_only: bool = False,
    ref_series: Any = None,
    api_version: str = "devel",
) -> datetime | None:
    """Fetch package list with build failures from the archive.

    Args:
        archive: The Launchpad archive to fetch from
        series: The distro series
        state: Build state to filter by
        last_published: Last published timestamp for incremental updates
        launchpad: Launchpad instance
        ubuntu: Ubuntu distribution object
        find_tagged_bugs: Tag to search for bugs
        packagesets: Dictionary of package sets
        packagesets_ftbfs: Dictionary to store FTBFS packages per packageset
        teams: Dictionary of teams
        teams_ftbfs: Dictionary to store FTBFS packages per team
        components: Dictionary to store packages per component
        arch_list: List of architectures to process
        main_archive: Main archive for comparison
        main_series: Main series for comparison
        release_only: Only include release pocket packages
        is_updates_archive: Whether this is an updates archive
        regressions_only: Only report regressions
        ref_series: Reference series for comparison
        api_version: API version string

    Returns:
        The last published timestamp of processed builds
    """
    print(f"Processing '{state}'")
    if last_published:
        last_published = last_published.replace(tzinfo=None)

    cur_last_published: datetime | None = None
    # XXX wgrant 2009-09-19: This is an awful hack. We should really
    # just let IArchive.getBuildRecords take a series argument.
    if archive.name == "primary":
        buildlist = series.getBuildRecords(build_state=state)
    else:
        buildlist = archive.getBuildRecords(build_state=state)

    for build in buildlist:
        if (
            last_published is not None
            and build.datebuilt is not None
            and last_published > build.datebuilt.replace(tzinfo=None)
        ):
            # leave the loop as we're past the last known published build record
            break

        csp_link = build.current_source_publication_link
        if not csp_link:
            # Build log for an older version
            continue

        if build.arch_tag not in (arch_list or []):
            print(f"  Skipping {build.title}")
            continue

        cur_last_published = build.datebuilt

        print(f"  {build.datebuilt} {build.title}")

        if is_updates_archive:
            if state == "Successfully built":
                update_builds[(build.source_package_name, build.arch_tag)] = build
                continue
        else:
            if (build.source_package_name, build.arch_tag) in update_builds:
                print(
                    f"    Skipping {build.source_package_name}, build succeeded in updates-archive"
                )
                continue

        spph = SPPH(
            csp_link,
            launchpad=launchpad,
            source_package_class=SourcePackage,
            ubuntu=ubuntu,
            find_tagged_bugs=find_tagged_bugs,
            packagesets=packagesets,
            packagesets_ftbfs=packagesets_ftbfs,
            teams=teams,
            teams_ftbfs=teams_ftbfs,
            components=components,
        )

        if spph.current is None:
            # If a main archive is specified, we check if the current source
            # is still published there. If it isn't, then it's out of date.
            # We should make this obvious.
            # The main archive will normally be the primary archive, and
            # probably only makes sense if the target archive is a rebuild.
            if main_archive:
                main_publications = main_archive.getPublishedSources(
                    distro_series=main_series,
                    exact_match=True,
                    source_name=spph._lp.source_package_name,
                    version=spph._lp.source_package_version,
                    status="Published",
                )
                spph.current = len(main_publications[:1]) > 0
            elif release_only:
                release_publications = archive.getPublishedSources(
                    distro_series=series,
                    pocket="Release",
                    exact_match=True,
                    source_name=spph._lp.source_package_name,
                    version=spph._lp.source_package_version,
                    status="Published",
                )
                spph.current = len(release_publications[:1]) > 0
                if not spph.current:
                    release_publications = archive.getPublishedSources(
                        distro_series=series,
                        pocket="Release",
                        exact_match=True,
                        source_name=spph._lp.source_package_name,
                        version=spph._lp.source_package_version,
                        status="Pending",
                    )
                    spph.current = len(release_publications[:1]) > 0
            else:
                spph.current = True

        if not spph.current:
            print("    superseded")

        no_regression = False
        if main_archive:
            # If this build failure is not a regression versus the
            # main archive, do not report it.
            main_builds = MainArchiveBuilds(
                main_archive,
                spph._lp.source_package_name,
                spph._lp.source_package_version,
            )
            try:
                if main_builds.results[build.arch_tag] != "Successfully built":
                    if regressions_only:
                        print(f"  Skipping {build.title}")
                        continue
                    else:
                        no_regression = True
            except KeyError:
                pass

        # set a never_built status
        already_built = False
        if ref_series:
            if main_archive:
                # test rebuild archive
                reference_archive = main_archive
                # search for successful build in ref_series
                ref_build = get_reference_build(
                    reference_archive,
                    ref_series,
                    ["Updates", "Release"],
                    build,
                    arch_list or [],
                )
                if ref_build:
                    already_built = True
                else:
                    # search for successful build in series
                    ref_build = get_reference_build(
                        reference_archive, series, ["Release"], build, arch_list or []
                    )
                    if ref_build:
                        already_built = True
                    else:
                        # search for successful build in Updates pocket
                        ref_build = get_reference_build(
                            reference_archive, series, ["Updates"], build, arch_list or []
                        )
                        if ref_build:
                            already_built = True
            else:
                # primary archive
                reference_archive = archive
                # search for successful build in ref_series
                ref_build = get_reference_build(
                    reference_archive,
                    ref_series,
                    ["Updates", "Release"],
                    build,
                    arch_list or [],
                )
                if ref_build:
                    already_built = True
                else:
                    # search for successful build in Release pocket
                    ref_build = get_reference_build(
                        reference_archive, series, ["Release"], build, arch_list or []
                    )
                    if spph.pocket == "Proposed" and ref_build:
                        already_built = True
                    else:
                        # search for successful build in Updates pocket, XXX same as above?
                        ref_build = get_reference_build(
                            reference_archive, series, ["Updates"], build, arch_list or []
                        )
                        if spph.pocket == "Proposed" and ref_build:
                            already_built = True
                    # no reason to look for spph.pocket == 'Proposed'
        never_built = not already_built
        if never_built:
            print("    never built before")

        spph.addBuildLog(build, never_built, no_regression, api_version)

    return cur_last_published


def get_reference_build(
    archive: Any,
    series: Any,
    pockets: list[str],
    build: Any,
    arch_list: list[str],
) -> Any:
    """Find a successful build in archive/series/pockets with build.arch_tag and build.source_package_name.

    Args:
        archive: The Launchpad archive to search
        series: The distro series
        pockets: List of pockets to search
        build: The build to find a reference for
        arch_list: List of architectures to consider

    Returns:
        A successful build record or None
    """
    print(
        f"    Find reference build: {build.source_package_name} / {build.arch_tag} / {pockets} / {series.name}"
    )
    # cache lookup
    br: Any = None
    for pocket in pockets:
        br = reference_builds.get((build.source_package_name, series.name, pocket, build.arch_tag))
        if br:
            try:
                print("        cache :", br.source_package_name, br.arch_tag)
            except ValueError:
                print("Unable to access :", build.source_package_name)
            return br

    if len(pockets) == 1:
        ref_sources = archive.getPublishedSources(
            source_name=build.source_package_name,
            exact_match=True,
            distro_series=series,
            status="Published",
            pocket=pockets[0],
        )
    else:
        ref_sources = archive.getPublishedSources(
            source_name=build.source_package_name,
            exact_match=True,
            distro_series=series,
            status="Published",
        )
    found = None
    for rs in ref_sources:
        if rs.pocket not in pockets:
            continue
        print(f"      v={rs.source_package_version}, {rs.pocket}")
        # getBuilds() doesn't find anything when a package was not (re)built in a series
        binaries = rs.getPublishedBinaries()
        for b in binaries:
            if b.is_debug:
                continue
            if b.pocket not in pockets:
                continue
            b_arch = b.distro_arch_series_link.split("/")[-1]
            if b_arch not in arch_list:
                continue

            # get the build, state is 'Successfully built'
            br = reference_builds.get((build.source_package_name, series.name, b.pocket, b_arch))
            if not br:
                br = b.build

            # print '          cand:', br.source_package_name, br.arch_tag, br.buildstate
            # cache br for any architecture in arch_list
            reference_builds[(build.source_package_name, series.name, b.pocket, b_arch)] = br

            try:
                if build.arch_tag == br.arch_tag:
                    found = br
            except ValueError:
                print("Unable to access :", build.source_package_name)
            # continue, so we don't call getPublishedSources/getPublishedBinaries for other archs again
            # break
        # only interested in the most recent published source
        break
    if found and br is not None:
        print("        found:", br.source_package_name, br.arch_tag)
    return found


def load_timestamps(name: str) -> dict[str, datetime | None]:
    """Load the saved timestamps about the last still published FTBFS build record.

    Args:
        name: The file name prefix for the timestamp file

    Returns:
        Dictionary mapping build states to timestamps
    """
    try:
        with open(f"{name}.json") as timestamp_file:
            tmp = json.load(timestamp_file)
        timestamps: dict[str, datetime | None] = {}
        for state, timestamp in list(tmp.items()):
            try:
                timestamps[state] = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
            except TypeError:
                timestamps[state] = None
        return timestamps
    except OSError:
        return {
            "Successfully built": None,
            "Failed to build": None,
            "Dependency wait": None,
            "Chroot problem": None,
            "Failed to upload": None,
            "Cancelled build": None,
        }


def save_timestamps(name: str, timestamps: dict[str, datetime | None]) -> None:
    """Save the timestamps of the last still published FTBFS build record into a JSON file.

    Args:
        name: The file name prefix for the timestamp file
        timestamps: Dictionary mapping build states to timestamps
    """
    with open(f"{name}.json", "w") as timestamp_file:
        tmp: dict[str, str | None] = {}
        for state, timestamp in list(timestamps.items()):
            if timestamp is not None:
                tmp[state] = timestamp.strftime("%s")
            else:
                tmp[state] = None
        json.dump(tmp, timestamp_file)


def clear_caches() -> None:
    """Clear all data caches."""
    update_builds.clear()
    reference_builds.clear()
