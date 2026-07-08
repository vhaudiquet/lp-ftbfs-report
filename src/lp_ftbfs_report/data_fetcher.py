#!/usr/bin/python3

# Copyright © 2007-2010 Michael Bienia <geser@ubuntu.com>
# Authors:
# Michael Bienia <geser@ubuntu.com>
# Andrea Gasparini <gaspa@yattaweb.it>
# License:
# GPLv2 (or later), see /usr/share/common-licenses/GPL

"""Data fetching functions for FTBFS report generator."""

from __future__ import annotations

import sys
from typing import Any

from lp_ftbfs_report.fetchers import BaseFetcher
from lp_ftbfs_report.models import SPPH, SourcePackage
from lp_ftbfs_report.progress import Progress


def fetch_pkg_list(
    state: str,
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
    verbose: bool = False,
    state_index: int | None = None,
    state_count: int | None = None,
) -> None:
    """Fetch package list with build failures.

    Args:
        state: Build state to filter by
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
        verbose: When True, emit per-build detail to stderr.
        state_index: 1-based index of this state within its phase.
        state_count: Number of states in this phase.
    """
    if fetcher is None:
        raise ValueError("fetcher must be provided")

    records = fetcher.get_build_records(state, arch_list)
    progress = Progress(
        records.total,
        state,
        verbose=verbose,
        state_index=state_index,
        state_count=state_count,
    )
    # Inject the tick callback so the fetcher advances the running counter
    # for every record pulled from the source collection, including those it
    # filters out (arch mismatch / superseded publications) and therefore
    # does not yield.
    records.on_item = progress.tick

    for build_record in records:
        # Handle updates archive logic
        if is_updates_archive:
            if state == "Successfully built":
                # Record successful build from updates archive
                if hasattr(fetcher, "record_update_build"):
                    fetcher.record_update_build(  # type: ignore[call-non-callable]
                        build_record.source_package_name, build_record.arch_tag, build_record
                    )
                progress.mark("skipped")
                continue
        else:
            # Check if build succeeded in updates archive
            if hasattr(
                fetcher, "check_update_archive_success"
            ) and fetcher.check_update_archive_success(  # type: ignore[call-non-callable]
                build_record.source_package_name, build_record.arch_tag
            ):
                if verbose:
                    print(
                        f"    Skipping {build_record.source_package_name}, "
                        "build succeeded in updates-archive",
                        file=sys.stderr,
                    )
                progress.mark("skipped")
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

        if not spph.current and verbose:
            print("    superseded", file=sys.stderr)

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
                    if verbose:
                        print(f"  Skipping {build_record.source_package_name}", file=sys.stderr)
                    progress.mark("skipped")
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
            if verbose:
                print("    never built before", file=sys.stderr)
            progress.mark("kept")
            progress.mark("never-built")
        else:
            progress.mark("kept")

        spph.addBuildLog(build_record, never_built, no_regression, api_version)

    progress.finish()
