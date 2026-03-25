#!/usr/bin/python3

# Copyright © 2007-2010 Michael Bienia <geser@ubuntu.com>
# Authors:
# Michael Bienia <geser@ubuntu.com>
# Andrea Gasparini <gaspa@yattaweb.it>
# License:
# GPLv2 (or later), see /usr/share/common-licenses/GPL

"""HTML generation for FTBFS report generator."""

from __future__ import annotations

import os
import time
from operator import methodcaller
from typing import Any

from jinja2 import Environment, FileSystemLoader

from lp_ftbfs_report.models import SourcePackage


def generate_page(
    name: str,
    archive: Any,
    updates_archive: Any,
    series: Any,
    archs_by_archive: dict[str, list[str]],
    main_archive: Any,
    components: dict[str, list[SourcePackage]],
    packagesets_ftbfs: dict[str, list[SourcePackage]],
    teams_ftbfs: dict[str, list[SourcePackage]],
    template: str = "build_status.html",
    arch_list: list[str] | None = None,
    notice: str | None = None,
    release_only: bool = False,
    ref_series: Any = None,
    generated: str = "",
) -> None:
    """Generate an HTML page with FTBFS report.

    Args:
        name: Output file name prefix
        archive: The Launchpad archive
        updates_archive: Updates archive (optional)
        series: Distro series
        archs_by_archive: Dictionary mapping archive type to architecture list
        main_archive: Main archive for comparison
        components: Dictionary of packages per component
        packagesets_ftbfs: Dictionary of FTBFS packages per packageset
        teams_ftbfs: Dictionary of FTBFS packages per team
        template: Jinja2 template file name
        arch_list: List of architectures to include
        notice: Optional HTML notice to include
        release_only: Whether to only include release pocket packages
        ref_series: Reference series for comparison
        generated: Generation timestamp string
    """
    if arch_list is None:
        arch_list = []

    def filter_ftbfs(pkglist: list[SourcePackage], current: bool) -> list[SourcePackage]:
        """Filter and sort packages that have FTBFS."""
        return list(
            filter(
                methodcaller("isFTBFS", arch_list, current),
                sorted(pkglist, key=lambda src: src.name),
            )
        )

    data: dict[str, Any] = {}
    for comp in ("main", "restricted", "universe", "multiverse"):
        data[comp] = filter_ftbfs(components.get(comp, []), True)
        data[f"{comp}_superseded"] = (
            filter_ftbfs(components.get(comp, []), False) if not release_only else []
        )
    for pkgset, pkglist in list(packagesets_ftbfs.items()):
        packagesets_ftbfs[pkgset] = filter_ftbfs(pkglist, True)
    for team, pkglist in list(teams_ftbfs.items()):
        teams_ftbfs[team] = filter_ftbfs(pkglist, True)

    # container object to hold the counts and the tooltip
    class StatData:
        def __init__(self, cnt: int | None, cnt_superseded: int | None, tooltip: str | None):
            self.cnt = cnt
            self.cnt_superseded = cnt_superseded
            self.tooltip = tooltip

    # compute some statistics (number of packages for each build failure type)
    stats = {}
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
        stats[state] = {}
        for arch in arch_list:
            tooltip = []
            cnt = 0
            cnt_sup = 0
            for comp in ("main", "restricted", "universe", "multiverse"):
                s = sum([pkg.getCount(arch, state) for pkg in data[comp]])
                s_sup = sum([pkg.getCount(arch, state) for pkg in data[f"{comp}_superseded"]])
                if s or s_sup:
                    cnt += s
                    cnt_sup += s_sup
                    tooltip.append(
                        f'<td>{comp}:</td><td style="text-align:right;">{s} ({s_sup} superseded)</td>'
                    )
            if cnt:
                tooltiphtml = "<table><tr>"
                tooltiphtml += "</tr><tr>".join(tooltip)
                tooltiphtml += "</tr></table>"
                stats[state][arch] = StatData(cnt, cnt_sup, tooltiphtml)
            else:
                stats[state][arch] = StatData(None, None, None)

    data["stats"] = stats
    data["archive"] = archive
    data["updates_archive"] = updates_archive
    data["main_archive"] = main_archive
    data["series"] = series
    data["arch_list"] = arch_list
    data["archs_by_archive"] = archs_by_archive
    data["lastupdate"] = time.strftime("%F %T %z")
    data["generated"] = generated
    data["packagesets"] = packagesets_ftbfs
    data["teams"] = teams_ftbfs
    data["notice"] = notice
    data["abbrs"] = {
        "FAILEDTOBUILD": "F",
        "CANCELLED": "X",
        "MANUALDEPWAIT": "M",
        "CHROOTWAIT": "C",
        "UPLOADFAIL": "U",
        "ALWAYSFTBFS": "F",
        "ALWAYSDEPWAIT": "M",
        "NOREGRFTBFS": "F",
        "NOREGRDEPWAIT": "M",
    }
    descr = f"Archive: {archive.displayname}"
    if updates_archive:
        descr += f" / Updates: {updates_archive.displayname}"
    if ref_series:
        descr += f" / Reference series: {ref_series}"
    if release_only:
        descr += " / Release only"
    data["description"] = descr

    env = Environment(loader=FileSystemLoader(os.path.dirname(__file__)))
    tmpl = env.get_template(template)
    stream = tmpl.render(**data)

    fn = f"{name}.html"
    output_path = os.path.join(os.path.dirname(__file__), fn)
    with open(f"{output_path}.new", "wb") as out:
        out.write(stream.encode("utf-8"))
    os.rename(f"{output_path}.new", output_path)
