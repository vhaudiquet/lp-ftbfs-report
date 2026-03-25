#!/usr/bin/python3

# Copyright © 2007-2010 Michael Bienia <geser@ubuntu.com>
# Authors:
# Michael Bienia <geser@ubuntu.com>
# Andrea Gasparini <gaspa@yattaweb.it>
# License:
# GPLv2 (or later), see /usr/share/common-licenses/GPL

"""Data models for FTBFS report generator."""

from __future__ import annotations

from typing import Any

import debian.debian_support
from launchpadlib.errors import HTTPError


def translate_api_web(self_url: str, api_version: str = "devel") -> str:
    """Translate an API URL to a web URL."""
    if self_url is None:
        return ""
    else:
        return self_url.replace("api.", "").replace(f"{api_version}/", "")


class PersonTeam:
    """Represents a person or team in Launchpad."""

    _cache: dict[str, PersonTeam | None] = {}
    display_name: str
    name: str

    def __new__(cls, personteam_link: str, launchpad: Any = None) -> PersonTeam | None:
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
        """Clear the cache."""
        cls._cache.clear()

    def __str__(self) -> str:
        return f"{self.display_name} ({self.name})"


class SourcePackage:
    """Represents a source package with FTBFS information."""

    _cache: dict[str, SourcePackage] = {}
    name: str
    url: str
    versions: VersionList
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
        ubuntu: Any = None,
        find_tagged_bugs: str | None = None,
        packagesets: dict[str, list[str]] | None = None,
        packagesets_ftbfs: dict[str, list[SourcePackage]] | None = None,
        teams: dict[str, list[str]] | None = None,
        teams_ftbfs: dict[str, list[SourcePackage]] | None = None,
        components: dict[str, list[SourcePackage]] | None = None,
    ) -> SourcePackage:
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
            cls._cache[spph.source_package_name] = srcpkg

            return srcpkg

    @classmethod
    def clear(cls) -> None:
        """Clear the cache."""
        cls._cache.clear()

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


class MainArchiveBuilds:
    """Cache for main archive build states."""

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
        """Clear the cache."""
        cls._cache.clear()


class SPPH:
    """Source Package Publishing History wrapper."""

    _cache: dict[str, SPPH] = {}  # dict with all SPPH objects
    _lp: Any
    logs: dict[str, SPPH.BuildLog]
    version: str
    pocket: str
    changed_by: PersonTeam | None
    current: bool | None

    def __new__(
        cls,
        spph_link: str,
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
            spph.changed_by = PersonTeam(lp_object.package_creator_link, launchpad=launchpad)
            spph.current = None

            # Create SourcePackage if class provided
            if source_package_class:
                source_package_class(
                    lp_object,
                    ubuntu=ubuntu,
                    find_tagged_bugs=find_tagged_bugs,
                    packagesets=packagesets,
                    packagesets_ftbfs=packagesets_ftbfs,
                    teams=teams,
                    teams_ftbfs=teams_ftbfs,
                    components=components,
                ).versions.append(spph)

            # add to cache
            cls._cache[spph_link] = spph

            return spph

    @classmethod
    def clear(cls) -> None:
        """Clear the cache."""
        cls._cache.clear()

    class BuildLog:
        """Represents a build log with state and URLs."""

        buildstate: str
        url: str
        log: str
        tooltip: str

        def __init__(
            self, build: Any, never_built: bool, no_regression: bool, api_version: str = "devel"
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
                if build.datebuilt:
                    self.tooltip = "Build finished on {}".format(
                        build.datebuilt.strftime("%Y-%m-%d %H:%M:%S UTC")
                    )
                else:
                    self.tooltip = "Build finish unknown"

    def addBuildLog(
        self, buildlog: Any, never_built: bool, no_regression: bool, api_version: str = "devel"
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
