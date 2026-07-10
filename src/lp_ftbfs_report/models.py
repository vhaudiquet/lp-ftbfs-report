#!/usr/bin/python3

# Copyright © 2007-2010 Michael Bienia <geser@ubuntu.com>
# Authors:
# Michael Bienia <geser@ubuntu.com>
# Andrea Gasparini <gaspa@yattaweb.it>
# License:
# GPLv2 (or later), see /usr/share/common-licenses/GPL

"""Data models for FTBFS report generator."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import debian.debian_support
from launchpadlib.errors import HTTPError

from lp_ftbfs_report.fetchers.base import BuildRecord


def translate_api_web(self_url: str | None, api_version: str = "devel") -> str:
    """Translate an API URL to a web URL."""
    if self_url is None:
        return ""
    else:
        return self_url.replace("api.", "").replace(f"{api_version}/", "")


@dataclass
class ModelCaches:
    """Per-run caches for the PersonTeam / SourcePackage / SPPH models.

    Held as an instance (threaded through the constructors via the ``caches``
    keyword argument) rather than as class-level module globals, so a process
    can run the report pipeline multiple times or embed the library without
    cross-run contamination. A fresh instance is empty by construction.
    """

    persons: dict[str, PersonTeam | None] = field(default_factory=dict)
    sources: dict[str, SourcePackage] = field(default_factory=dict)
    spphs: dict[str, SPPH] = field(default_factory=dict)

    def clear(self) -> None:
        """Empty all three caches."""
        self.persons.clear()
        self.sources.clear()
        self.spphs.clear()


class PersonTeam:
    """Represents a person or team in Launchpad."""

    display_name: str
    name: str

    def __new__(
        cls,
        personteam_link: str,
        *,
        caches: ModelCaches,
        launchpad: Any = None,
    ) -> PersonTeam | None:
        if personteam_link in caches.persons:
            return caches.persons[personteam_link]
        try:
            personteam = super().__new__(cls)

            # fill the new PersonTeam object with data
            lp_object = launchpad.load(personteam_link)
            personteam.display_name = lp_object.display_name
            personteam.name = lp_object.name

        except HTTPError as e:
            if e.response.status in (404, 410):
                personteam = None
            else:
                raise

        # add to cache
        caches.persons[personteam_link] = personteam

        return personteam

    def __str__(self) -> str:
        return f"{self.display_name} ({self.name})"


class SourcePackage:
    """Represents a source package with FTBFS information."""

    name: str
    url: str
    versions: SourcePackage.VersionList
    tagged_bugs: list[Any]
    packagesets: set[str]
    teams: set[str]

    class VersionList(list):
        """A list that keeps versions sorted."""

        def append(self, item: SPPH) -> None:
            super().append(item)
            self.sort(key=lambda x: debian.debian_support.Version(x.version))

    def __new__(
        cls,
        spph: Any,
        *,
        caches: ModelCaches,
        ubuntu: Any = None,
        find_tagged_bugs: str | None = None,
        packagesets: dict[str, list[str]] | None = None,
        packagesets_ftbfs: dict[str, list[SourcePackage]] | None = None,
        teams: dict[str, list[str]] | None = None,
        teams_ftbfs: dict[str, list[SourcePackage]] | None = None,
        components: dict[str, list[SourcePackage]] | None = None,
    ) -> SourcePackage:
        if spph.source_package_name in caches.sources:
            return caches.sources[spph.source_package_name]
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
            for (ps, srcpkglist) in list((packagesets or {}).items())
            if spph.source_package_name in srcpkglist
        }
        if components and spph.component_name in components:
            components[spph.component_name].append(srcpkg)
        for ps in srcpkg.packagesets:
            if packagesets_ftbfs is not None and ps in packagesets_ftbfs:
                packagesets_ftbfs[ps].append(srcpkg)

        srcpkg.teams = {
            team
            for (team, srcpkglist) in list((teams or {}).items())
            if spph.source_package_name in srcpkglist and spph.component_name == "main"
        }
        for team in srcpkg.teams:
            if teams_ftbfs is not None and team in teams_ftbfs:
                teams_ftbfs[team].append(srcpkg)

        # add to cache
        caches.sources[spph.source_package_name] = srcpkg

        return srcpkg

    def isFTBFS(self, arch_list: list[str] | None = None, current: bool = True) -> bool:
        """Returns True if at least one FTBFS exists."""
        for ver in self.versions:
            if ver.current != current:
                continue
            for arch in arch_list or []:
                log = ver.getArch(arch)
                if log is not None:
                    return True
        return False

    def getCount(self, arch: str, state: str) -> int:
        """Get count of builds with a specific state for an architecture."""
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


class SPPH:
    """Source Package Publishing History wrapper."""

    _lp: Any
    logs: dict[str, SPPH.BuildLog]
    version: str
    pocket: str
    changed_by: PersonTeam | None
    current: bool | None

    def __new__(
        cls,
        spph_link: str,
        *,
        caches: ModelCaches,
        launchpad: Any = None,
        source_package_class: type[SourcePackage] | None = None,
        ubuntu: Any = None,
        find_tagged_bugs: str | None = None,
        packagesets: dict[str, list[str]] | None = None,
        packagesets_ftbfs: dict[str, list[SourcePackage]] | None = None,
        teams: dict[str, list[str]] | None = None,
        teams_ftbfs: dict[str, list[SourcePackage]] | None = None,
        components: dict[str, list[SourcePackage]] | None = None,
    ) -> SPPH:
        if spph_link in caches.spphs:
            return caches.spphs[spph_link]
        spph = super().__new__(cls)

        # fill the new SPPH object with data
        lp_object = launchpad.load(spph_link)
        spph._lp = lp_object
        spph.logs = {}
        spph.version = lp_object.source_package_version
        spph.pocket = lp_object.pocket
        spph.changed_by = PersonTeam(
            lp_object.package_creator_link, caches=caches, launchpad=launchpad
        )
        spph.current = None

        # Create SourcePackage if class provided
        if source_package_class:
            source_package_class(
                lp_object,
                caches=caches,
                ubuntu=ubuntu,
                find_tagged_bugs=find_tagged_bugs,
                packagesets=packagesets,
                packagesets_ftbfs=packagesets_ftbfs,
                teams=teams,
                teams_ftbfs=teams_ftbfs,
                components=components,
            ).versions.append(spph)

        # add to cache
        caches.spphs[spph_link] = spph

        return spph

    class BuildLog:
        """Represents a build log with state and URLs."""

        buildstate: str
        url: str
        log: str
        tooltip: str

        def __init__(
            self,
            build: BuildRecord,
            never_built: bool,
            no_regression: bool,
            api_version: str = "devel",
        ) -> None:
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
            self.url = translate_api_web(build.self_link, api_version)

            if self.buildstate == "UPLOADFAIL":
                self.log = translate_api_web(build.upload_log_url, api_version)
            else:
                if build.build_log_url:
                    self.log = translate_api_web(build.build_log_url, api_version)
                else:
                    self.log = ""

            if self.buildstate in ("MANUALDEPWAIT", "ALWAYSDEPWAIT", "NOREGRDEPWAIT"):
                self.tooltip = f"waits on {build.dependencies}"
            elif build.datebuilt is None:
                self.tooltip = "Broken build"
            else:
                self.tooltip = (
                    f"Build finished on {build.datebuilt.strftime('%Y-%m-%d %H:%M:%S UTC')}"
                )

    def addBuildLog(
        self,
        buildlog: BuildRecord,
        never_built: bool,
        no_regression: bool,
        api_version: str = "devel",
    ) -> None:
        """Add a build log entry."""
        self.logs[buildlog.arch_tag] = self.BuildLog(
            buildlog, never_built, no_regression, api_version
        )

    def getArch(self, arch: str) -> BuildLog | None:
        """Get build log for a specific architecture."""
        return self.logs.get(arch)

    def getChangedBy(self) -> str:
        """Returns a string with the person who changed this package."""
        return f"Changed-By: {self.changed_by}"
