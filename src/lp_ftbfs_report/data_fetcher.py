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

from lp_ftbfs_report.fetchers import BaseFetcher
from lp_ftbfs_report.models import SPPH, SourcePackage


def fetch_pkg_list(
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
    arch_list: list[str],
    main_archive: Any = None,
    is_updates_archive: bool = False,
    regressions_only: bool = False,
    ref_series: Any = None,
    api_version: str = "devel",
    fetcher: BaseFetcher | None = None,
) -> datetime | None:
    """Fetch package list with build failures.

    Args:
        state: Build state to filter by
        last_published: Last published timestamp for incremental updates
        launchpad: Launchpad instance (for model compatibility)
        ubuntu: Ubuntu distribution (for model compatibility)
        find_tagged_bugs: Tag to search for bugs
        packagesets: Dictionary of package sets
        packagesets_ftbfs: Dictionary to store FTBFS packages per packageset
        teams: Dictionary of teams
        teams_ftbfs: Dictionary to store FTBFS packages per team
        components: Dictionary to store packages per component
        arch_list: List of architectures to process
        main_archive: Main archive for comparison
        is_updates_archive: Whether this is an updates archive
        regressions_only: Only report regressions
        ref_series: Reference series for comparison
        api_version: API version string
        fetcher: Data fetcher instance

    Returns:
        The last published timestamp of processed builds
    """
    if fetcher is None:
        raise ValueError("fetcher must be provided")

    cur_last_published: datetime | None = None

    # Get build records from fetcher
    for build_record in fetcher.get_build_records(state, arch_list, last_published):
        cur_last_published = build_record.datebuilt

        # Handle updates archive logic
        if is_updates_archive:
            if state == "Successfully built":
                # Record successful build from updates archive
                if hasattr(fetcher, "record_update_build"):
                    fetcher.record_update_build(  # type: ignore[call-non-callable]
                        build_record.source_package_name, build_record.arch_tag, build_record
                    )
                continue
        else:
            # Check if build succeeded in updates archive
            if hasattr(
                fetcher, "check_update_archive_success"
            ) and fetcher.check_update_archive_success(  # type: ignore[call-non-callable]
                build_record.source_package_name, build_record.arch_tag
            ):
                print(
                    f"    Skipping {build_record.source_package_name}, "
                    "build succeeded in updates-archive"
                )
                continue

        # Load SPPH and create SourcePackage
        csp_link = build_record.current_source_publication_link
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

        # Check current publication status
        if spph.current is None:
            spph.current = fetcher.check_current_publication(
                spph._lp.source_package_name, spph._lp.source_package_version, spph.pocket
            )

        if not spph.current:
            print("    superseded")

        # Check for regressions
        no_regression = False
        if main_archive:
            main_build_state = fetcher.get_main_archive_build_state(
                spph._lp.source_package_name,
                spph._lp.source_package_version,
                build_record.arch_tag,
            )
            if main_build_state and main_build_state != "Successfully built":
                if regressions_only:
                    print(f"  Skipping {build_record.source_package_name}")
                    continue
                else:
                    no_regression = True

        # Check if never built before
        never_built = True
        if ref_series:
            ref_build = fetcher.find_reference_build(
                build_record.source_package_name,
                build_record.arch_tag,
                ["Updates", "Release"],
            )
            if ref_build:
                never_built = False

        if never_built:
            print("    never built before")

        spph.addBuildLog(build_record, never_built, no_regression, api_version)

    return cur_last_published


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
