#!/usr/bin/python3

# Copyright © 2007-2010 Michael Bienia <geser@ubuntu.com>
# Authors:
# Michael Bienia <geser@ubuntu.com>
# Andrea Gasparini <gaspa@yattaweb.it>
# License:
# GPLv2 (or later), see /usr/share/common-licenses/GPL

"""CSV generation for FTBFS report generator."""

from __future__ import annotations

import csv
import os

from lp_ftbfs_report.models import SourcePackage


def generate_csvfile(
    name: str, components: dict[str, list[SourcePackage]], output_dir: str | None = None
) -> None:
    """Generate a CSV file with FTBFS report data.

    Args:
        name: Output file name prefix
        components: Dictionary of packages per component
        output_dir: Output directory (defaults to the current working directory)
    """
    if output_dir is None:
        output_dir = os.getcwd()

    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"{name}.csv")

    with open(output_path, "w", newline="") as csvout:
        writer = csv.writer(csvout)
        for comp in list(components.values()):
            for pkg in comp:
                for ver in pkg.versions:
                    for state in (
                        "FAILEDTOBUILD",
                        "MANUALDEPWAIT",
                        "CHROOTWAIT",
                        "UPLOADFAIL",
                        "CANCELLED",
                        "ALWAYSFTBFS",
                        "ALWAYSDEPWAIT",
                        "NOREGRFTBFS",
                        "NOREGRDEPWAIT",
                    ):
                        archs = [
                            arch
                            for (arch, log) in list(ver.logs.items())
                            if log.buildstate == state
                        ]
                        if archs:
                            log = ver.logs[archs[0]].log
                            writer.writerow(
                                [pkg.name, log, "[{}] {}".format(", ".join(archs), state)]
                            )
