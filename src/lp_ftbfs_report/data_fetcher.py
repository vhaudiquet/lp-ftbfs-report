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
from dataclasses import dataclass, field
from typing import Any

from lp_ftbfs_report.fetchers import BaseFetcher
from lp_ftbfs_report.models import SPPH, ModelCaches, SourcePackage
from lp_ftbfs_report.progress import Progress


@dataclass
class ReportAccumulators:
    """Mutable containers shared across build-state passes.

    Built up by :func:`fetch_pkg_list` as it walks each build state; read by
    the HTML/CSV generators afterwards.
    """

    components: dict[str, list[SourcePackage]]
    packagesets: dict[str, list[str]]
    packagesets_ftbfs: dict[str, list[SourcePackage]]
    teams: dict[str, list[str]]
    teams_ftbfs: dict[str, list[SourcePackage]]


@dataclass
class FetchContext:
    """Run-wide invariant context for :func:`fetch_pkg_list`.

    Holds the values that do not change between the updates-archive and main
    archive passes (or between build states), so the per-call signature stays
    small.
    """

    launchpad: Any
    ubuntu: Any
    main_archive: Any
    ref_series: Any
    find_tagged_bugs: str | None
    caches: ModelCaches
    api_version: str = "devel"
    verbose: bool = False
    regressions_only: bool = False
    # Successful builds found in the updates archive, keyed by
    # (source_name, arch_tag).  Populated during the updates-archive pass and
    # consulted during the main-archive pass to skip builds already fixed
    # there.  Lives on the shared context, not on a fetcher instance, so both
    # passes see the same dict even though they use different fetchers.
    update_builds: dict[tuple[str, str], Any] = field(default_factory=dict)


def fetch_pkg_list(
    state: str,
    arch_list: list[str],
    fetcher: BaseFetcher,
    accumulators: ReportAccumulators,
    ctx: FetchContext,
    *,
    is_updates_archive: bool = False,
    state_index: int | None = None,
    state_count: int | None = None,
) -> None:
    """Fetch package list with build failures.

    Args:
        state: Build state to filter by
        arch_list: List of architectures to process
        fetcher: Data fetcher instance yielding build records
        accumulators: Shared mutable containers (components, packagesets, teams)
        ctx: Run-wide invariant context (launchpad, archives, flags, ...)
        is_updates_archive: Whether this is an updates archive pass
        state_index: 1-based index of this state within its phase
        state_count: Number of states in this phase
    """
    records = fetcher.get_build_records(state, arch_list)
    progress = Progress(
        records.total,
        state,
        verbose=ctx.verbose,
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
                # Record successful build from updates archive so the
                # main-archive pass can skip packages already fixed there.
                ctx.update_builds[(build_record.source_package_name, build_record.arch_tag)] = (
                    build_record
                )
                progress.mark("skipped")
                continue
        else:
            # Skip packages that already succeeded in the updates archive.
            if (build_record.source_package_name, build_record.arch_tag) in ctx.update_builds:
                if ctx.verbose:
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
            caches=ctx.caches,
            launchpad=ctx.launchpad,
            source_package_class=SourcePackage,
            ubuntu=ctx.ubuntu,
            find_tagged_bugs=ctx.find_tagged_bugs,
            packagesets=accumulators.packagesets,
            packagesets_ftbfs=accumulators.packagesets_ftbfs,
            teams=accumulators.teams,
            teams_ftbfs=accumulators.teams_ftbfs,
            components=accumulators.components,
        )

        # Check current publication status
        if spph.current is None:
            spph.current = fetcher.check_current_publication(
                spph.source_package_name, spph.source_package_version, spph.pocket
            )

        if not spph.current and ctx.verbose:
            print("    superseded", file=sys.stderr)

        # Check for regressions
        no_regression = False
        if ctx.main_archive:
            main_build_state = fetcher.get_main_archive_build_state(
                spph.source_package_name,
                spph.source_package_version,
                build_record.arch_tag,
            )
            if main_build_state and main_build_state != "Successfully built":
                if ctx.regressions_only:
                    if ctx.verbose:
                        print(f"  Skipping {build_record.source_package_name}", file=sys.stderr)
                    progress.mark("skipped")
                    continue
                else:
                    no_regression = True

        # Check if never built before
        never_built = True
        if ctx.ref_series:
            ref_build = fetcher.find_reference_build(
                build_record.source_package_name,
                build_record.arch_tag,
                ["Updates", "Release"],
            )
            if ref_build:
                never_built = False

        if never_built:
            if ctx.verbose:
                print("    never built before", file=sys.stderr)
            progress.mark("kept")
            progress.mark("never-built")
        else:
            progress.mark("kept")

        spph.addBuildLog(build_record, never_built, no_regression, ctx.api_version)

    progress.finish()
