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

from datetime import datetime, timezone
from optparse import OptionParser
from typing import Any

import requests
from launchpadlib.errors import HTTPError
from launchpadlib.launchpad import Launchpad

from lp_ftbfs_report.csv_generator import generate_csvfile
from lp_ftbfs_report.data_fetcher import (
    clear_caches,
    fetch_pkg_list,
    load_timestamps,
    save_timestamps,
)
from lp_ftbfs_report.html_generator import generate_page
from lp_ftbfs_report.models import SPPH, MainArchiveBuilds, PersonTeam, SourcePackage

# Configuration constants
LP_SERVICE = "production"
API_VERSION = "devel"
FIND_TAGGED_BUGS = "ftbfs"


def main() -> None:
    """Main entry point for the FTBFS report generator."""
    # login anonymously to LP
    launchpad = Launchpad.login_anonymously("qa-ftbfs", LP_SERVICE, version=API_VERSION)

    ubuntu = launchpad.distributions["ubuntu"]

    usage = "usage: %prog [options] <archive> <series> <arch> [<arch> ...]"
    parser = OptionParser(usage=usage)
    parser.add_option("-f", "--filename", dest="name", help="File name prefix for the result.")
    parser.add_option(
        "-n",
        "--notice",
        dest="notice_file",
        help="HTML notice file to include in the page header.",
    )
    parser.add_option(
        "--regressions-only",
        dest="regressions_only",
        action="store_true",
        default=False,
        help="Only report build regressions, compared to the main archive.",
    )
    parser.add_option(
        "--release-only",
        dest="release_only",
        action="store_true",
        help="Only include sources currently published in the release pocket.",
    )
    parser.add_option(
        "--updates-archive", dest="updates_archive", help="Name of an updates archive."
    )
    parser.add_option(
        "--reference-series",
        dest="ref_series",
        help="Name of the series to look for successful builds.",
    )
    (options, args) = parser.parse_args()
    if len(args) < 3:
        parser.error("Need at least 3 arguments.")

    try:
        archive = ubuntu.getArchive(name=args[0])
    except HTTPError:
        print(f"Error: {args[0]} is not a valid archive.")
        return

    if options.updates_archive:
        try:
            updates_archive = ubuntu.getArchive(name=options.updates_archive)
        except HTTPError:
            print(f"Error: {options.updates_archive} is not a valid archive.")
            return
    else:
        updates_archive = None
        print("no updates-archive is used")

    if options.ref_series:
        try:
            ref_series = ubuntu.getSeries(name_or_version=options.ref_series)
        except HTTPError:
            print(f"Error: {options.ref_series} is not a valid series.")
            return
    else:
        ref_series = None
        print("no reference series is used")

    try:
        series = ubuntu.getSeries(name_or_version=args[1])
    except HTTPError:
        print(f"Error: {args[1]} is not a valid series.")
        return

    if options.name is None:
        options.name = f"{archive.name}-{series.name}"

    if archive.name != "primary":
        main_archive = ubuntu.main_archive
        main_series = series
    else:
        main_archive = main_series = None

    archs_by_archive: dict[str, list[str]] = {"main": [], "ports": []}
    default_arch_list: list[str] = []
    for arch in args[2:]:
        das = series.getDistroArchSeries(archtag=arch)
        archs_by_archive[das.official and "main" or "ports"].append(arch)
    default_arch_list.extend(archs_by_archive["main"])
    default_arch_list.extend(archs_by_archive["ports"])

    generated_info = datetime.now(timezone.utc).strftime("Started: %Y-%m-%d %X")

    # Use the archive and series directly (no need for a loop)
    print(f"Generating FTBFS for {series.fullseriesname}")

    # clear all caches
    PersonTeam.clear()
    SourcePackage.clear()
    SPPH.clear()
    MainArchiveBuilds.clear()
    clear_caches()
    last_published = load_timestamps(options.name)

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
    for ps in launchpad.packagesets:
        if ps.distroseries_link == series.self_link:
            packagesets[ps.name] = ps.getSourcesIncluded(direct_inclusion=False)
            packagesets_ftbfs[ps.name] = []  # empty list to add FTBFS for each package set later

    teams = requests.get(
        "https://people.canonical.com/~ubuntu-archive/package-team-mapping.json"
    ).json()

    # Per team list of FTBFS
    teams_ftbfs: dict[str, list[SourcePackage]] = {team: [] for team in teams}

    if updates_archive:
        print("XXX: processing updates archive ...")
        last_updates_published: dict[str, Any] = {
            "Successfully built": None,
            "Failed to build": None,
            "Dependency wait": None,
            "Chroot problem": None,
            "Failed to upload": None,
            "Cancelled build": None,
        }
        for state in (
            "Successfully built",
            "Failed to build",
            "Dependency wait",
            "Chroot problem",
            "Failed to upload",
            "Cancelled build",
        ):
            last_updates_published[state] = fetch_pkg_list(
                updates_archive,
                series,
                state,
                last_updates_published[state],
                launchpad,
                ubuntu,
                FIND_TAGGED_BUGS,
                packagesets,
                packagesets_ftbfs,
                teams,
                teams_ftbfs,
                components,
                default_arch_list,
                main_archive,
                main_series,
                options.release_only,
                is_updates_archive=True,
                regressions_only=options.regressions_only,
                ref_series=ref_series,
                api_version=API_VERSION,
            )

    print("XXX: processing archive ...")
    for state in (
        "Failed to build",
        "Dependency wait",
        "Chroot problem",
        "Failed to upload",
        "Cancelled build",
    ):
        last_published[state] = fetch_pkg_list(
            archive,
            series,
            state,
            last_published[state],
            launchpad,
            ubuntu,
            FIND_TAGGED_BUGS,
            packagesets,
            packagesets_ftbfs,
            teams,
            teams_ftbfs,
            components,
            default_arch_list,
            main_archive,
            main_series,
            options.release_only,
            regressions_only=options.regressions_only,
            ref_series=ref_series,
            api_version=API_VERSION,
        )

    save_timestamps(options.name, last_published)

    if options.notice_file:
        with open(options.notice_file) as f:
            notice = f.read()
    else:
        notice = None

    generated_info += datetime.now(timezone.utc).strftime("  /  Finished: %Y-%m-%d %X")

    print("Generating HTML page...")
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
    )
    print("Generating CSV file...")
    generate_csvfile(options.name, components)


if __name__ == "__main__":
    main()
