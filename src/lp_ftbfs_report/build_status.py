#!/usr/bin/python3

# Copyright © 2007-2010 Michael Bienia <geser@ubuntu.com>
# Authors:
# Michael Bienia <geser@ubuntu.com>
# Andrea Gasparini <gaspa@yattaweb.it>
# License:
# GPLv2 (or later), see /usr/share/common-licenses/GPL

# Rewrite of the old build_status script using LP API

# Requirements:
# - python3-debian
# - python3-jinja2
# - python3-launchpadlib
# - python3-requests

# Uncomment for tracing LP API calls
# import httplib2
# httplib2.debuglevel = 1

"""Main entry point for FTBFS report generator."""

from __future__ import annotations

import os
import sys
from argparse import SUPPRESS, ArgumentParser
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from launchpadlib.errors import HTTPError
from launchpadlib.launchpad import Launchpad

from lp_ftbfs_report.csv_generator import generate_csvfile
from lp_ftbfs_report.data_fetcher import FetchContext, ReportAccumulators, fetch_pkg_list
from lp_ftbfs_report.fetchers import (
    BaseFetcher,
    DummyFetcher,
    PPAFetcher,
    TestRebuildFetcher,
    parse_ppa_spec,
)
from lp_ftbfs_report.html_generator import generate_page
from lp_ftbfs_report.models import ModelCaches, SourcePackage
from lp_ftbfs_report.report_data import print_summary, serialize_report, write_json

# Configuration constants
LP_SERVICE = "production"
API_VERSION = "devel"
FIND_TAGGED_BUGS = "ftbfs"


@dataclass
class ReportSetup:
    """Resolved fetcher, archives, series and arch args for a report run.

    Returned by :func:`setup_fetcher_and_context`. ``main_series`` is not
    included: it is only used while building the fetcher and is already held
    on the fetcher instance.
    """

    fetcher: BaseFetcher
    updates_fetcher: BaseFetcher | None
    archive: Any
    series: Any
    launchpad: Any
    ubuntu: Any
    main_archive: Any
    updates_archive: Any
    ref_series: Any
    arch_args: list[str]


def setup_fetcher_and_context(
    options: Any, args: list[str], launchpad: Any, ubuntu: Any, api_version: str
) -> ReportSetup | None:
    """Set up the appropriate fetcher and context for the selected mode.

    Args:
        options: Parsed command-line options
        args: Remaining positional arguments
        launchpad: Launchpad instance
        ubuntu: Ubuntu distribution
        api_version: API version string

    Returns:
        A :class:`ReportSetup` with the fetcher, archives, series and arch
        args for the run, or ``None`` on a setup error (already reported
        to stderr).
    """
    main_archive = None
    main_series = None
    updates_archive = None
    updates_fetcher = None
    ref_series = None

    if options.ppa_spec:
        # PPA mode
        print(f"PPA mode: {options.ppa_spec}", file=sys.stderr)
        try:
            ppa_owner, ppa_name = parse_ppa_spec(options.ppa_spec)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return None

        series_name = args[0]
        arch_args = args[1:]

        try:
            fetcher = PPAFetcher(
                launchpad=launchpad,
                ubuntu=ubuntu,
                ppa_owner=ppa_owner,
                ppa_name=ppa_name,
                series_name=series_name,
                api_version=api_version,
                verbose=options.verbose,
            )
        except (HTTPError, ValueError) as e:
            print(f"Error: {e}", file=sys.stderr)
            return None

        archive = fetcher.ppa
        series = fetcher.series

        if options.name is None:
            options.name = f"ppa-{ppa_owner}-{ppa_name}-{series.name}"

        options.regressions_only = False

    elif options.dummy_fixture:
        # Dummy data mode
        print(f"Dummy data mode: {options.dummy_fixture}", file=sys.stderr)
        series_name = args[0]
        arch_args = args[1:]

        try:
            fetcher = DummyFetcher(
                options.dummy_fixture, api_version=api_version, verbose=options.verbose
            )
        except (OSError, ValueError) as e:
            print(f"Error loading dummy data: {e}", file=sys.stderr)
            return None

        series = fetcher.create_mock_series()
        archive = fetcher.create_mock_archive()
        launchpad = fetcher.create_mock_launchpad()
        ubuntu = launchpad

        if options.name is None:
            archive_info = fetcher.get_archive_info()
            options.name = f"{archive_info.name}-{series.name}"

        options.regressions_only = False

    else:
        # Standard test rebuild mode
        archive_name = args[0]
        series_name = args[1]
        arch_args = args[2:]

        try:
            archive = ubuntu.getArchive(name=archive_name)
        except HTTPError:
            print(f"Error: {archive_name} is not a valid archive.", file=sys.stderr)
            return None

        if options.updates_archive:
            try:
                updates_archive = ubuntu.getArchive(name=options.updates_archive)
            except HTTPError:
                print(f"Error: {options.updates_archive} is not a valid archive.", file=sys.stderr)
                return None
        else:
            print("no updates-archive is used", file=sys.stderr)

        if options.ref_series:
            try:
                ref_series = ubuntu.getSeries(name_or_version=options.ref_series)
            except HTTPError:
                print(f"Error: {options.ref_series} is not a valid series.", file=sys.stderr)
                return None
        else:
            print("no reference series is used", file=sys.stderr)

        try:
            series = ubuntu.getSeries(name_or_version=series_name)
        except HTTPError:
            print(f"Error: {series_name} is not a valid series.", file=sys.stderr)
            return None

        if options.name is None:
            options.name = f"{archive.name}-{series.name}"

        if archive.name != "primary":
            main_archive = ubuntu.main_archive
            main_series = series
        else:
            main_archive = main_series = None

        fetcher = TestRebuildFetcher(
            launchpad=launchpad,
            ubuntu=ubuntu,
            archive=archive,
            series=series,
            main_archive=main_archive,
            main_series=main_series,
            ref_series=ref_series,
            release_only=options.release_only,
            regressions_only=options.regressions_only,
            api_version=api_version,
            verbose=options.verbose,
        )

        updates_fetcher = None
        if updates_archive:
            updates_fetcher = TestRebuildFetcher(
                launchpad=launchpad,
                ubuntu=ubuntu,
                archive=updates_archive,
                series=series,
                main_archive=None,
                main_series=None,
                ref_series=None,
                release_only=options.release_only,
                regressions_only=options.regressions_only,
                api_version=api_version,
                verbose=options.verbose,
            )

    return ReportSetup(
        fetcher=fetcher,
        updates_fetcher=updates_fetcher,
        archive=archive,
        series=series,
        launchpad=launchpad,
        ubuntu=ubuntu,
        main_archive=main_archive,
        updates_archive=updates_archive,
        ref_series=ref_series,
        arch_args=arch_args,
    )


# Build-state tuples for the two fetch passes.
# The updates-archive pass includes "Successfully built" so it can record
# packages that already succeeded there; the main-archive pass skips those.
UPDATES_ARCHIVE_STATES = (
    "Successfully built",
    "Failed to build",
    "Dependency wait",
    "Chroot problem",
    "Failed to upload",
    "Cancelled build",
)
ARCHIVE_STATES = (
    "Failed to build",
    "Dependency wait",
    "Chroot problem",
    "Failed to upload",
    "Cancelled build",
)


def _build_parser() -> ArgumentParser:
    """Build the command-line argument parser."""
    usage = (
        "%(prog)s [options] <archive> <series> <arch> [<arch> ...]\n"
        "       %(prog)s --ppa <owner/ppaname> <series> <arch> [<arch> ...]\n"
        "       %(prog)s --dummy-data <fixture-file> <series> <arch> [<arch> ...]"
    )
    parser = ArgumentParser(usage=usage)
    parser.add_argument("-f", "--filename", dest="name", help="File name prefix for the result.")
    parser.add_argument(
        "-n", "--notice", dest="notice_file", help="HTML notice file to include in the page header."
    )
    parser.add_argument(
        "--regressions-only",
        dest="regressions_only",
        action="store_true",
        default=False,
        help="Only report build regressions, compared to the main archive.",
    )
    parser.add_argument(
        "--release-only",
        dest="release_only",
        action="store_true",
        default=False,
        help="Only include sources currently published in the release pocket.",
    )
    parser.add_argument(
        "--updates-archive", dest="updates_archive", help="Name of an updates archive."
    )
    parser.add_argument(
        "--reference-series",
        dest="ref_series",
        help="Name of the series to look for successful builds.",
    )
    parser.add_argument(
        "--ppa",
        dest="ppa_spec",
        help="Generate report for a PPA. Format: owner/ppaname or ppa:owner/ppaname",
    )
    parser.add_argument(
        "--dummy-data",
        dest="dummy_fixture",
        help="Use dummy data from JSON fixture file for testing.",
    )
    parser.add_argument(
        "--output-dir",
        dest="output_dir",
        default=None,
        help="Directory where generated HTML and CSV reports are written (defaults to the current directory).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        dest="verbose",
        action="store_true",
        default=False,
        help="Print per-build detail (never-built, reference-build lookups, etc.). "
        "Without this flag only a compact progress line per build state is shown.",
    )
    parser.add_argument(
        "--json-only",
        dest="json_only",
        action="store_true",
        default=False,
        help="Only fetch data and write the JSON file; skip HTML and CSV generation.",
    )
    # Positional arguments are mode-dependent (archive/series/arch), validated
    # manually in main(); the multi-mode usage string above documents them.
    parser.add_argument("args", nargs="*", help=SUPPRESS)
    return parser


def _order_archs(series: Any, arch_args: list[str]) -> list[str]:
    """Order architectures: officially supported first, then unofficial.

    ``DistroArchSeries.official`` marks architectures Ubuntu officially
    supports; they appear before unofficial ones in the report.
    """
    official: list[str] = []
    unofficial: list[str] = []
    for arch in arch_args:
        das = series.getDistroArchSeries(archtag=arch)
        (official if das.official else unofficial).append(arch)
    return [*official, *unofficial]


def _init_accumulators(
    fetcher: BaseFetcher,
) -> tuple[
    dict[str, list[SourcePackage]],
    dict[str, list[SourcePackage]],
    dict[str, list[SourcePackage]],
    ReportAccumulators,
]:
    """Create the components/packagesets/teams accumulators for a run.

    Returns ``(components, packagesets_ftbfs, teams_ftbfs, accumulators)``.
    """
    components: dict[str, list[SourcePackage]] = {
        "main": [],
        "restricted": [],
        "universe": [],
        "multiverse": [],
    }
    packagesets = fetcher.get_packagesets()
    packagesets_ftbfs: dict[str, list[SourcePackage]] = {ps: [] for ps in packagesets}
    teams = fetcher.get_teams()
    teams_ftbfs: dict[str, list[SourcePackage]] = {team: [] for team in teams}
    accumulators = ReportAccumulators(
        components=components,
        packagesets=packagesets,
        packagesets_ftbfs=packagesets_ftbfs,
        teams=teams,
        teams_ftbfs=teams_ftbfs,
    )
    return components, packagesets_ftbfs, teams_ftbfs, accumulators


def _run_fetches(
    fetcher: BaseFetcher,
    updates_fetcher: BaseFetcher | None,
    updates_archive: Any,
    arch_list: list[str],
    accumulators: ReportAccumulators,
    ctx: FetchContext,
) -> None:
    """Run the updates-archive and main-archive build-state passes."""
    if updates_archive:
        print("Processing updates archive ...", file=sys.stderr)
        # updates_fetcher is set together with updates_archive in
        # setup_fetcher_and_context, so it is non-None here.
        assert updates_fetcher is not None
        for i, state in enumerate(UPDATES_ARCHIVE_STATES, start=1):
            fetch_pkg_list(
                state=state,
                arch_list=arch_list,
                fetcher=updates_fetcher,
                accumulators=accumulators,
                ctx=ctx,
                is_updates_archive=True,
                state_index=i,
                state_count=len(UPDATES_ARCHIVE_STATES),
            )

    print("Processing archive ...", file=sys.stderr)
    for i, state in enumerate(ARCHIVE_STATES, start=1):
        fetch_pkg_list(
            state=state,
            arch_list=arch_list,
            fetcher=fetcher,
            accumulators=accumulators,
            ctx=ctx,
            state_index=i,
            state_count=len(ARCHIVE_STATES),
        )


def _build_meta(
    options: Any,
    archive: Any,
    updates_archive: Any,
    main_archive: Any,
    series: Any,
    arch_list: list[str],
    notice: str | None,
    generated_started: str,
    generated_finished: str,
) -> dict:
    """Build the metadata dict serialized alongside the report data."""
    return {
        "name": options.name,
        "schema_version": 1,
        "generated_started": generated_started,
        "generated_finished": generated_finished,
        "archive": {"name": archive.name, "displayname": archive.displayname},
        "updates_archive": (
            {"name": updates_archive.name, "displayname": updates_archive.displayname}
            if updates_archive
            else None
        ),
        "main_archive": (
            {"name": main_archive.name, "displayname": main_archive.displayname}
            if main_archive
            else None
        ),
        "series": {"name": series.name, "fullseriesname": series.fullseriesname},
        "arch_list": arch_list,
        "notice": notice,
        "release_only": bool(options.release_only),
        "ref_series": options.ref_series,
    }


def main() -> None:
    """Main entry point for the FTBFS report generator."""
    parser = _build_parser()
    options = parser.parse_args()
    args = options.args

    # Validate positional args per mode
    if options.ppa_spec:
        if len(args) < 2:
            parser.error("PPA mode needs at least 2 arguments: <series> <arch> [<arch> ...]")
    elif options.dummy_fixture:
        if len(args) < 2:
            parser.error("Dummy mode needs at least 2 arguments: <series> <arch> [<arch> ...]")
    else:
        if len(args) < 3:
            parser.error("Need at least 3 arguments: <archive> <series> <arch> [<arch> ...]")

    # Login to Launchpad only if not in dummy mode (dummy mode uses mock objects)
    if options.dummy_fixture:
        launchpad = None
        ubuntu = None
    else:
        launchpad = Launchpad.login_anonymously("qa-ftbfs", LP_SERVICE, version=API_VERSION)
        ubuntu = launchpad.distributions["ubuntu"]

    result = setup_fetcher_and_context(options, args, launchpad, ubuntu, API_VERSION)
    if result is None:
        return

    default_arch_list = _order_archs(result.series, result.arch_args)

    generated_started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")

    print(f"Generating FTBFS for {result.series.fullseriesname}", file=sys.stderr)

    # Per-run model caches, held as an instance rather than module globals so
    # the pipeline is reusable in-process without cross-run contamination.
    caches = ModelCaches()
    components, packagesets_ftbfs, teams_ftbfs, accumulators = _init_accumulators(result.fetcher)

    ctx = FetchContext(
        launchpad=result.launchpad,
        ubuntu=result.ubuntu,
        main_archive=result.main_archive,
        ref_series=result.ref_series,
        find_tagged_bugs=FIND_TAGGED_BUGS,
        caches=caches,
        api_version=API_VERSION,
        verbose=options.verbose,
        regressions_only=options.regressions_only,
    )

    _run_fetches(
        result.fetcher,
        result.updates_fetcher,
        result.updates_archive,
        default_arch_list,
        accumulators,
        ctx,
    )

    if options.notice_file:
        with open(options.notice_file) as f:
            notice = f.read()
    else:
        notice = None

    generated_finished = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")

    # ── Step 1: Serialize aggregated data to JSON ────────────────────────────
    out_dir = os.path.abspath(options.output_dir if options.output_dir is not None else os.getcwd())
    os.makedirs(out_dir, exist_ok=True)

    meta = _build_meta(
        options,
        result.archive,
        result.updates_archive,
        result.main_archive,
        result.series,
        default_arch_list,
        notice,
        generated_started,
        generated_finished,
    )
    json_path = os.path.join(out_dir, f"{options.name}.json")
    print("Writing JSON data file...", file=sys.stderr)
    write_json(serialize_report(components, packagesets_ftbfs, teams_ftbfs, meta), json_path)

    if options.json_only:
        print_summary("Data aggregation complete!", [("JSON", json_path)])
        return

    # ── Step 2: Render HTML and CSV from in-memory objects ───────────────────
    print("Generating HTML page...", file=sys.stderr)
    generate_page(
        options.name,
        result.archive,
        result.updates_archive,
        result.series,
        result.main_archive,
        components,
        packagesets_ftbfs,
        teams_ftbfs,
        arch_list=default_arch_list,
        notice=notice,
        generated_started=generated_started,
        generated_finished=generated_finished,
        ref_series=options.ref_series,
        output_dir=options.output_dir,
    )
    print("Generating CSV file...", file=sys.stderr)
    generate_csvfile(options.name, components, output_dir=options.output_dir)

    html_path = os.path.join(out_dir, f"{options.name}.html")
    csv_path = os.path.join(out_dir, f"{options.name}.csv")
    print_summary(
        "Report generation complete!",
        [("JSON", json_path), ("HTML", html_path), ("CSV", csv_path)],
        out_dir,
    )


if __name__ == "__main__":
    main()
