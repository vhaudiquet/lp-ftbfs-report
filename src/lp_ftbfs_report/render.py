#!/usr/bin/python3

# Copyright © 2007-2010 Michael Bienia <geser@ubuntu.com>
# Authors:
# Michael Bienia <geser@ubuntu.com>
# Andrea Gasparini <gaspa@yattaweb.it>
# License:
# GPLv2 (or later), see /usr/share/common-licenses/GPL

"""Render HTML and CSV reports from a previously fetched JSON data file.

This is the second step of the two-step pipeline:

  Step 1 – lp-ftbfs-report (or lp-ftbfs-report --json-only):
              fetch from Launchpad / PPA / dummy data → <name>.json

  Step 2 – lp-ftbfs-render <name>.json:
              read <name>.json → <name>.html + <name>.csv

Usage
-----
    lp-ftbfs-render [options] <json-file>

Options
-------
    --output-dir DIR   Where to write HTML and CSV (default: same directory as
                       the JSON file).
"""

from __future__ import annotations

import os
import sys
from argparse import ArgumentParser

from lp_ftbfs_report.csv_generator import generate_csvfile
from lp_ftbfs_report.html_generator import generate_page
from lp_ftbfs_report.report_data import deserialize_report, print_summary, read_json


def main() -> None:
    """Main entry point for rendering HTML/CSV from a JSON report file."""
    usage = "%(prog)s [options] <json-file>"
    parser = ArgumentParser(usage=usage)
    parser.add_argument(
        "--output-dir",
        dest="output_dir",
        default=None,
        help=(
            "Directory where generated HTML and CSV reports are written "
            "(defaults to the directory that contains the JSON file)."
        ),
    )
    parser.add_argument("json_file", help="JSON report file to render")
    options = parser.parse_args()

    json_path = os.path.abspath(options.json_file)
    if not os.path.exists(json_path):
        print(f"Error: JSON file not found: {json_path}", file=sys.stderr)
        sys.exit(1)

    # Default output directory: same directory as the JSON file.
    if options.output_dir is None:
        options.output_dir = os.path.dirname(json_path)

    out_dir = os.path.abspath(options.output_dir)

    print(f"Reading report data from {json_path} ...")
    data = read_json(json_path)
    components, packagesets_ftbfs, teams_ftbfs, render_kwargs = deserialize_report(data)

    # Pop "name" out of render_kwargs; generate_page() takes it as a positional arg.
    name = render_kwargs.pop("name")

    print("Generating HTML page...")
    generate_page(
        name,
        render_kwargs.pop("archive"),
        render_kwargs.pop("updates_archive"),
        render_kwargs.pop("series"),
        render_kwargs.pop("archs_by_archive"),
        render_kwargs.pop("main_archive"),
        components,
        packagesets_ftbfs,
        teams_ftbfs,
        output_dir=out_dir,
        **render_kwargs,
    )

    print("Generating CSV file...")
    generate_csvfile(name, components, output_dir=out_dir)

    html_path = os.path.join(out_dir, f"{name}.html")
    csv_path = os.path.join(out_dir, f"{name}.csv")
    print_summary(
        "Report generation complete!",
        [("HTML", html_path), ("CSV", csv_path)],
        out_dir,
    )


if __name__ == "__main__":
    main()
