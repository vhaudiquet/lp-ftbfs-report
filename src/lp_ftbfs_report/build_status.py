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

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from operator import methodcaller
from optparse import OptionParser
from typing import Any

# import apt_pkg
import debian.debian_support
import requests
from jinja2 import Environment, FileSystemLoader
from launchpadlib.errors import HTTPError
from launchpadlib.launchpad import Launchpad

lp_service = "production"
api_version = "devel"
default_arch_list: list[str] = []
find_tagged_bugs = "ftbfs"

# Global variables for package tracking
launchpad: Any = None
ubuntu: Any = None
packagesets: dict[str, list[str]] = {}
packagesets_ftbfs: dict[str, list[SourcePackage]] = {}
teams: dict[str, list[str]] = {}
teams_ftbfs: dict[str, list[SourcePackage]] = {}
components: dict[str, list[SourcePackage]] = {}
# apt_pkg.init_system()


# copied from ubuntu-dev-tools, libsupport.py:
def translate_api_web(self_url):
    if self_url is None:
        return ""
    else:
        return self_url.replace("api.", "").replace(f"{api_version}/", "")


class PersonTeam:
    _cache: dict[str, PersonTeam | None] = {}
    display_name: str
    name: str

    def __new__(cls, personteam_link: str) -> PersonTeam | None:
        try:
            return cls._cache[personteam_link]
        except KeyError:
            try:
                personteam = super().__new__(cls)

                # fill the new PersonTeam object with data
                lp_object = launchpad.load(personteam_link)
                personteam.display_name = lp_object.display_name
                personteam.name = lp_object.name

            except KeyError:
                return None
            except HTTPError as e:
                if e.response.status in (404, 410):
                    personteam = None
                else:
                    raise

            # add to cache
            cls._cache[personteam_link] = personteam

            return personteam

    @classmethod
    def clear(cls) -> None:
        cls._cache.clear()

    def __str__(self) -> str:
        return f"{self.display_name} ({self.name})"


class SourcePackage:
    _cache: dict[str, SourcePackage] = {}
    name: str
    url: str
    versions: VersionList
    tagged_bugs: list[Any]
    packagesets: set[str]
    teams: set[str]

    class VersionList(list):
        def append(self, item: SPPH) -> None:
            super().append(item)
            # self.sort(key = attrgetter('version'), cmp = apt_pkg.version_compare)
            # self.sort(key = attrgetter('version'))
            self.sort(key=lambda x: debian.debian_support.Version(x.version))

            # self.sort(key = functools.cmp_to_key(apt_pkg.version_compare))
            # TypeError: a bytes-like object is required, not 'SPPH'

    def __new__(cls, spph: Any) -> SourcePackage:
        try:
            return cls._cache[spph.source_package_name]
        except KeyError:
            srcpkg = super().__new__(cls)

            # fill the new SourcePackage object with data
            srcpkg.name = spph.source_package_name
            srcpkg.url = f"https://launchpad.net/ubuntu/+source/{srcpkg.name}"
            srcpkg.versions = cls.VersionList()
            if find_tagged_bugs is None:
                srcpkg.tagged_bugs = []
            else:
                ts = ubuntu.getSourcePackage(name=srcpkg.name).searchTasks(tags=find_tagged_bugs)
                srcpkg.tagged_bugs = [t.bug for t in ts]
            srcpkg.packagesets = {
                ps
                for (ps, srcpkglist) in list(packagesets.items())
                if spph.source_package_name in srcpkglist
            }
            components[spph.component_name].append(srcpkg)
            for ps in srcpkg.packagesets:
                packagesets_ftbfs[ps].append(srcpkg)

            srcpkg.teams = {
                team
                for (team, srcpkglist) in list(teams.items())
                if spph.source_package_name in srcpkglist and spph.component_name == "main"
            }
            for team in srcpkg.teams:
                teams_ftbfs[team].append(srcpkg)

            # add to cache
            cls._cache[spph.source_package_name] = srcpkg

            return srcpkg

    @classmethod
    def clear(cls) -> None:
        cls._cache.clear()

    def isFTBFS(self, arch_list: list[str] | None = None, current: bool = True) -> bool:
        """Returns True if at least one FTBFS exists."""
        if arch_list is None:
            arch_list = default_arch_list
        for ver in self.versions:
            if ver.current != current:
                continue
            for arch in arch_list:
                log = ver.getArch(arch)
                if log is not None:
                    return True
        return False

    def getCount(self, arch: str, state: str) -> int:
        count = 0
        for ver in self.versions:
            if arch in ver.logs and ver.logs[arch].buildstate == state:
                count += 1
        return count

    def getPackagesets(self, name: str | None = None) -> list[str]:
        """Return the list of packagesets without the packageset `name`."""
        if name is None:
            return list(self.packagesets)
        else:
            return list(self.packagesets.difference((name,)))


class MainArchiveBuilds:
    _cache: dict[str, MainArchiveBuilds] = {}
    results: dict[str, str]

    def __new__(cls, main_archive: Any, source: str, version: str) -> MainArchiveBuilds:
        try:
            return cls._cache[f"{source},{version}"]
        except KeyError:
            bfm = super().__new__(cls)
            results: dict[str, str] = {}
            sourcepubs = main_archive.getPublishedSources(
                exact_match=True, source_name=source, version=version
            )
            for pub in sourcepubs:
                for build in pub.getBuilds():
                    # assumes sourcepubs are sorted latest release to oldest,
                    # so first record wins
                    if build.arch_tag not in results:
                        results[build.arch_tag] = build.buildstate
            bfm.results = results
            # add to cache
            cls._cache[f"{source},{version}"] = bfm

            return bfm

    @classmethod
    def clear(cls) -> None:
        cls._cache.clear()


class SPPH:
    _cache: dict[str, SPPH] = {}  # dict with all SPPH objects
    _lp: Any
    logs: dict[str, SPPH.BuildLog]
    version: str
    pocket: str
    changed_by: PersonTeam | None
    current: bool | None

    def __new__(cls, spph_link: str) -> SPPH:
        try:
            return cls._cache[spph_link]
        except KeyError:
            spph = super().__new__(cls)

            # fill the new SPPH object with data
            lp_object = launchpad.load(spph_link)
            spph._lp = lp_object
            spph.logs = {}
            spph.version = lp_object.source_package_version
            spph.pocket = lp_object.pocket
            spph.changed_by = PersonTeam(lp_object.package_creator_link)
            # spph.signed_by = spph._lp.package_signer_link and PersonTeam(lp_object.package_signer_link)
            spph.current = None
            SourcePackage(lp_object).versions.append(spph)

            # add to cache
            cls._cache[spph_link] = spph

            return spph

    @classmethod
    def clear(cls) -> None:
        cls._cache.clear()

    class BuildLog:
        buildstate: str
        url: str
        log: str
        tooltip: str

        def __init__(self, build: Any, never_built: bool, no_regression: bool) -> None:
            buildstates = {
                "Failed to build": "FAILEDTOBUILD",
                "Dependency wait": "MANUALDEPWAIT",
                "Chroot problem": "CHROOTWAIT",
                "Failed to upload": "UPLOADFAIL",
                "Cancelled build": "CANCELLED",
                "Always FTBFS": "ALWAYSFTBFS",
                "Always DepWait": "ALWAYSDEPWAIT",
                "NoRegr FTBFS": "NOREGRFTBFS",
                "NoRegr DepWait": "NOREGRDEPWAIT",
            }
            self.buildstate = buildstates[build.buildstate]
            if no_regression and self.buildstate == "FAILEDTOBUILD":
                self.buildstate = "NOREGRFTBFS"
            elif no_regression and self.buildstate == "MANUALDEPWAIT":
                self.buildstate = "NOREGRDEPWAIT"

            # overriding regression status with never_built status
            if never_built and self.buildstate == "FAILEDTOBUILD":
                self.buildstate = "ALWAYSFTBFS"
            elif never_built and self.buildstate == "MANUALDEPWAIT":
                self.buildstate = "ALWAYSDEPWAIT"
            self.url = translate_api_web(build.self_link)

            if self.buildstate == "UPLOADFAIL":
                self.log = translate_api_web(build.upload_log_url)
            else:
                if build.build_log_url:
                    self.log = translate_api_web(build.build_log_url)
                else:
                    self.log = ""

            if self.buildstate in ("MANUALDEPWAIT", "ALWAYSDEPWAIT", "NOREGRDEPWAIT"):
                self.tooltip = f"waits on {build.dependencies}"
            elif build.datebuilt is None:
                self.tooltip = "Broken build"
            else:
                if build.datebuilt:
                    self.tooltip = "Build finished on {}".format(
                        build.datebuilt.strftime("%Y-%m-%d %H:%M:%S UTC")
                    )
                else:
                    self.tooltip = "Build finish unknown"

    def addBuildLog(self, buildlog: Any, never_built: bool, no_regression: bool) -> None:
        self.logs[buildlog.arch_tag] = self.BuildLog(buildlog, never_built, no_regression)

    def getArch(self, arch: str) -> BuildLog | None:
        return self.logs.get(arch)

    def getChangedBy(self) -> str:
        """
        Returns a string with the person who changed this package.
        """
        return f"Changed-By: {self.changed_by}"


# cache: (source_package_name, arch_tag) -> build
update_builds: dict[tuple[str, str], Any] = {}


def fetch_pkg_list(
    archive: Any,
    series: Any,
    state: str,
    last_published: datetime | None,
    arch_list: list[str] | None = None,
    main_archive: Any = None,
    main_series: Any = None,
    release_only: bool = False,
    is_updates_archive: bool = False,
    regressions_only: bool = False,
    ref_series: Any = None,
) -> datetime | None:
    print(f"Processing '{state}'")
    if last_published:
        last_published = last_published.replace(tzinfo=None)

    if arch_list is None:
        arch_list = default_arch_list

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

        if build.arch_tag not in arch_list:
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

        spph = SPPH(csp_link)

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
                    arch_list,
                )
                if ref_build:
                    already_built = True
                else:
                    # search for successful build in series
                    ref_build = get_reference_build(
                        reference_archive, series, ["Release"], build, arch_list
                    )
                    if ref_build:
                        already_built = True
                    else:
                        # search for successful build in Updates pocket
                        ref_build = get_reference_build(
                            reference_archive, series, ["Updates"], build, arch_list
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
                    arch_list,
                )
                if ref_build:
                    already_built = True
                else:
                    # search for successful build in Release pocket
                    ref_build = get_reference_build(
                        reference_archive, series, ["Release"], build, arch_list
                    )
                    if spph.pocket == "Proposed" and ref_build:
                        already_built = True
                    else:
                        # search for successful build in Updates pocket, XXX same as above?
                        ref_build = get_reference_build(
                            reference_archive, series, ["Updates"], build, arch_list
                        )
                        if spph.pocket == "Proposed" and ref_build:
                            already_built = True
                    # no reason to look for spph.pocket == 'Proposed'
        never_built = not already_built
        if never_built:
            print("    never built before")

        SPPH(csp_link).addBuildLog(build, never_built, no_regression)

    return cur_last_published


# cache: (source_package_name, series, pocket, arch_tag) -> build
reference_builds: dict[tuple[str, str, str, str], Any] = {}


def get_reference_build(
    archive: Any,
    series: Any,
    pockets: list[str],
    build: Any,
    arch_list: list[str],
) -> Any:
    """find a successful build in archive/series/pockets with build.arch_tag and build.source_package_name"""

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


def generate_page(
    name: str,
    archive: Any,
    updates_archive: Any,
    series: Any,
    archs_by_archive: dict[str, list[str]],
    main_archive: Any,
    template: str = "build_status.html",
    arch_list: list[str] | None = None,
    notice: str | None = None,
    release_only: bool = False,
    regressions_only: bool = False,
    ref_series: Any = None,
    generated: str = "",
) -> None:
    if arch_list is None:
        arch_list = default_arch_list

    def filter_ftbfs(pkglist: list[SourcePackage], current: bool) -> list[SourcePackage]:
        # sort the package lists
        return list(
            filter(
                methodcaller("isFTBFS", arch_list, current),
                sorted(pkglist, key=lambda src: src.name),
            )
        )

    data: dict[str, Any] = {}
    for comp in ("main", "restricted", "universe", "multiverse"):
        data[comp] = filter_ftbfs(components[comp], True)
        data[f"{comp}_superseded"] = (
            filter_ftbfs(components[comp], False) if not release_only else []
        )
    for pkgset, pkglist in list(packagesets_ftbfs.items()):
        packagesets_ftbfs[pkgset] = filter_ftbfs(pkglist, True)
    for team, pkglist in list(teams_ftbfs.items()):
        teams_ftbfs[team] = filter_ftbfs(pkglist, True)

    # container object to hold the counts and the tooltip
    class StatData:
        def __init__(self, cnt, cnt_superseded, tooltip):
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
    if regressions_only:
        descr += " / Only report regressions"
    data["description"] = descr

    env = Environment(loader=FileSystemLoader("."))
    tmpl = env.get_template(template)
    stream = tmpl.render(**data)

    fn = f"../{name}.html"
    with open(f"{fn}.new", "wb") as out:
        out.write(stream.encode("utf-8"))
    os.rename(f"{fn}.new", fn)


def generate_csvfile(name):
    with open(f"../{name}.csv", "w") as csvout:
        linetemplate = "%(name)s,%(link)s,%(explain)s\n"
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
                            csvout.write(
                                linetemplate
                                % {
                                    "name": pkg.name,
                                    "link": log,
                                    "explain": "[{}] {}".format(", ".join(archs), state),
                                }
                            )


def load_timestamps(name: str) -> dict[str, datetime | None]:
    """Load the saved timestamps about the last still published FTBFS build record."""
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
    """Save the timestamps of the last still published FTBFS build record into a JSON file."""
    with open(f"{name}.json", "w") as timestamp_file:
        tmp: dict[str, str | None] = {}
        for state, timestamp in list(timestamps.items()):
            if timestamp is not None:
                tmp[state] = timestamp.strftime("%s")
            else:
                tmp[state] = None
        json.dump(tmp, timestamp_file)


def main() -> None:
    """Main entry point for the FTBFS report generator."""
    global launchpad, ubuntu

    # login anonymously to LP
    launchpad = Launchpad.login_anonymously("qa-ftbfs", lp_service, version=api_version)

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

    if options.updates_archive:
        try:
            updates_archive = ubuntu.getArchive(name=options.updates_archive)
        except HTTPError:
            print(f"Error: {options.updates_archive} is not a valid archive.")
    else:
        updates_archive = None
        print("no updates-archive is used")

    if options.ref_series:
        try:
            ref_series = ubuntu.getSeries(name_or_version=options.ref_series)
        except HTTPError:
            print(f"Error: {options.ref_series} is not a valid series.")
    else:
        ref_series = None
        print("no reference series is used")

    try:
        series = ubuntu.getSeries(name_or_version=args[1])
    except HTTPError:
        print(f"Error: {args[1]} is not a valid series.")

    if options.name is None:
        options.name = f"{archive.name}-{series.name}"

    if archive.name != "primary":
        main_archive = ubuntu.main_archive
        main_series = series
    else:
        main_archive = main_series = None

    archs_by_archive = {"main": [], "ports": []}
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
    last_published = load_timestamps(options.name)

    # list of SourcePackages for each component
    global packagesets, packagesets_ftbfs, teams, teams_ftbfs, components
    components = {
        "main": [],
        "restricted": [],
        "universe": [],
        "multiverse": [],
    }

    # packagesets for this series
    packagesets = {}
    packagesets_ftbfs = {}
    for ps in launchpad.packagesets:
        if ps.distroseries_link == series.self_link:
            packagesets[ps.name] = ps.getSourcesIncluded(direct_inclusion=False)
            packagesets_ftbfs[ps.name] = []  # empty list to add FTBFS for each package set later

    teams = requests.get(
        "https://people.canonical.com/~ubuntu-archive/package-team-mapping.json"
    ).json()

    # Per team list of FTBFS
    teams_ftbfs = {team: [] for team in teams}

    if updates_archive:
        print("XXX: processing updates archive ...")
        last_updates_published = {
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
                default_arch_list,
                main_archive,
                main_series,
                options.release_only,
                is_updates_archive=True,
                regressions_only=options.regressions_only,
                ref_series=ref_series,
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
            default_arch_list,
            main_archive,
            main_series,
            options.release_only,
            regressions_only=options.regressions_only,
            ref_series=ref_series,
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
        notice=notice,
        release_only=options.release_only,
        ref_series=options.ref_series,
        generated=generated_info,
    )
    print("Generating CSV file...")
    generate_csvfile(options.name)


if __name__ == "__main__":
    main()
