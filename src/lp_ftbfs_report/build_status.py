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

import requests
from launchpadlib.errors import HTTPError
from launchpadlib.launchpad import Launchpad

from lp_ftbfs_report.csv_generator import generate_csvfile
from lp_ftbfs_report.data_fetcher import fetch_pkg_list
from lp_ftbfs_report.fetchers import (
    BaseFetcher,
    DummyFetcher,
    PPAFetcher,
    TestRebuildFetcher,
    parse_ppa_spec,
)
from lp_ftbfs_report.html_generator import generate_page
from lp_ftbfs_report.models import SPPH, PersonTeam, SourcePackage

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
        Tuple of (fetcher, updates_fetcher, archive, series, launchpad, ubuntu,
                 main_archive, main_series, updates_archive, ref_series, arch_args)
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


def main() -> None:
    """Main entry point for the FTBFS report generator."""
    usage = (
        "%(prog)s [options] <archive> <series> <arch> [<arch> ...]\n"
        "       %(prog)s --ppa <owner/ppaname> <series> <arch> [<arch> ...]\n"
        "       %(prog)s --dummy-data <fixture-file> <series> <arch> [<arch> ...]"
    )
    parser = ArgumentParser(usage=usage)
    parser.add_argument("-f", "--filename", dest="name", help="File name prefix for the result.")
    parser.add_argument(
        "-n",
        "--notice",
        dest="notice_file",
        help="HTML notice file to include in the page header.",
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
        help="Directory where generated HTML and CSV reports are written (defaults to the package directory).",
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
    # Positional arguments are mode-dependent (archive/series/arch), validated
    # manually below; the multi-mode usage string above documents them.
    parser.add_argument("args", nargs="*", help=SUPPRESS)
    options = parser.parse_args()
    args = options.args

    # Determine mode based on flags
    if options.ppa_spec:
        # PPA mode: ppa_spec, series, arch(s)
        if len(args) < 2:
            parser.error("PPA mode needs at least 2 arguments: <series> <arch> [<arch> ...]")
    elif options.dummy_fixture:
        # Dummy mode: series, arch(s)
        if len(args) < 2:
            parser.error("Dummy mode needs at least 2 arguments: <series> <arch> [<arch> ...]")
    else:
        # Standard mode: archive, series, arch(s)
        if len(args) < 3:
            parser.error("Need at least 3 arguments: <archive> <series> <arch> [<arch> ...]")

    # Login to Launchpad only if not in dummy mode (dummy mode uses mock objects)
    if options.dummy_fixture:
        launchpad = None
        ubuntu = None
    else:
        # login anonymously to LP
        launchpad = Launchpad.login_anonymously("qa-ftbfs", LP_SERVICE, version=API_VERSION)
        ubuntu = launchpad.distributions["ubuntu"]

    # Set up fetcher and context based on mode
    result = setup_fetcher_and_context(options, args, launchpad, ubuntu, API_VERSION)
    if result is None:
        return

    fetcher = result.fetcher
    updates_fetcher = result.updates_fetcher
    archive = result.archive
    series = result.series
    launchpad = result.launchpad
    ubuntu = result.ubuntu
    main_archive = result.main_archive
    updates_archive = result.updates_archive
    ref_series = result.ref_series
    arch_args = result.arch_args

    # Process architecture list
    archs_by_archive: dict[str, list[str]] = {"main": [], "ports": []}
    default_arch_list: list[str] = []
    for arch in arch_args:
        das = series.getDistroArchSeries(archtag=arch)
        archs_by_archive["main" if das.official else "ports"].append(arch)
    default_arch_list.extend(archs_by_archive["main"])
    default_arch_list.extend(archs_by_archive["ports"])

    generated_info = datetime.now(timezone.utc).strftime("Started: %Y-%m-%d %X")

    # Use the archive and series directly (no need for a loop)
    print(f"Generating FTBFS for {series.fullseriesname}", file=sys.stderr)

    # clear all caches
    PersonTeam.clear()
    SourcePackage.clear()
    SPPH.clear()
    # list of SourcePackages for each component
    components: dict[str, list[SourcePackage]] = {
        "main": [],
        "restricted": [],
        "universe": [],
        "multiverse": [],
    }

    # packagesets for this series
    packagesets: dict[str, list[str]] = {}
    packagesets_ftbfs: dict[str, list[SourcePackage]] = {}
    if fetcher:
        # Use fetcher to get packagesets
        packagesets = fetcher.get_packagesets()
        for ps_name in packagesets:
            packagesets_ftbfs[ps_name] = []
    else:
        # Load from Launchpad
        for ps in launchpad.packagesets:
            if ps.distroseries_link == series.self_link:
                packagesets[ps.name] = ps.getSourcesIncluded(direct_inclusion=False)
                packagesets_ftbfs[ps.name] = []

    # Get teams
    if fetcher:
        teams = fetcher.get_teams()
    else:
        teams = requests.get(
            "https://people.canonical.com/~ubuntu-archive/package-team-mapping.json"
        ).json()

    # Per team list of FTBFS
    teams_ftbfs: dict[str, list[SourcePackage]] = {team: [] for team in teams}

    if updates_archive:
        print("Processing updates archive ...", file=sys.stderr)
        updates_states = (
            "Successfully built",
            "Failed to build",
            "Dependency wait",
            "Chroot problem",
            "Failed to upload",
            "Cancelled build",
        )
        for i, state in enumerate(updates_states, start=1):
            fetch_pkg_list(
                state=state,
                launchpad=launchpad,
                ubuntu=ubuntu,
                find_tagged_bugs=FIND_TAGGED_BUGS,
                packagesets=packagesets,
                packagesets_ftbfs=packagesets_ftbfs,
                teams=teams,
                teams_ftbfs=teams_ftbfs,
                components=components,
                arch_list=default_arch_list,
                main_archive=main_archive,
                is_updates_archive=True,
                regressions_only=options.regressions_only,
                ref_series=ref_series,
                api_version=API_VERSION,
                fetcher=updates_fetcher,
                verbose=options.verbose,
                state_index=i,
                state_count=len(updates_states),
            )

    print("Processing archive ...", file=sys.stderr)
    archive_states = (
        "Failed to build",
        "Dependency wait",
        "Chroot problem",
        "Failed to upload",
        "Cancelled build",
    )
    for i, state in enumerate(archive_states, start=1):
        fetch_pkg_list(
            state=state,
            launchpad=launchpad,
            ubuntu=ubuntu,
            find_tagged_bugs=FIND_TAGGED_BUGS,
            packagesets=packagesets,
            packagesets_ftbfs=packagesets_ftbfs,
            teams=teams,
            teams_ftbfs=teams_ftbfs,
            components=components,
            arch_list=default_arch_list,
            main_archive=main_archive,
            is_updates_archive=False,
            regressions_only=options.regressions_only,
            ref_series=ref_series,
            api_version=API_VERSION,
            fetcher=fetcher,
            verbose=options.verbose,
            state_index=i,
            state_count=len(archive_states),
        )

    if options.notice_file:
        with open(options.notice_file) as f:
            notice = f.read()
    else:
        notice = None

    generated_info += datetime.now(timezone.utc).strftime("  /  Finished: %Y-%m-%d %X")

    print("Generating HTML page...", file=sys.stderr)
    generate_page(
        options.name,
        archive,
        updates_archive,
        series,
        archs_by_archive,
        main_archive,
        components,
        packagesets_ftbfs,
        teams_ftbfs,
        arch_list=default_arch_list,
        notice=notice,
        release_only=options.release_only,
        ref_series=options.ref_series,
        generated=generated_info,
        output_dir=options.output_dir,
    )
    print("Generating CSV file...", file=sys.stderr)
    generate_csvfile(options.name, components, output_dir=options.output_dir)

    # Resolve the actual output directory used (mirrors the default in the generators)
    out_dir = os.path.abspath(options.output_dir if options.output_dir is not None else os.getcwd())
    html_path = os.path.join(out_dir, f"{options.name}.html")
    csv_path = os.path.join(out_dir, f"{options.name}.csv")

    GREEN = "\033[32m"
    CYAN = "\033[36m"
    BOLD = "\033[1m"
    RESET = "\033[0m"
    CHECK = "\u2714"

    print()
    print(f"{BOLD}{GREEN}{CHECK}  Report generation complete!{RESET}")
    print(f"   {CYAN}HTML{RESET}   {html_path}")
    print(f"   {CYAN}CSV{RESET}    {csv_path}")
    for asset in sorted(os.listdir(os.path.join(os.path.dirname(__file__), "html"))):
        print(f"   {CYAN}ASSET{RESET}  {os.path.join(out_dir, asset)}")
    print()


if __name__ == "__main__":
    main()
