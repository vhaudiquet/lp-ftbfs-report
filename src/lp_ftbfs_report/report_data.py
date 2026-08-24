#!/usr/bin/python3

# Copyright © 2007-2010 Michael Bienia <geser@ubuntu.com>
# Authors:
# Michael Bienia <geser@ubuntu.com>
# Andrea Gasparini <gaspa@yattaweb.it>
# License:
# GPLv2 (or later), see /usr/share/common-licenses/GPL

"""Serialization and deserialization of report data to/from JSON.

This module is the bridge between the two pipeline steps:

  Step 1 – Fetch:   network / Launchpad API  →  JSON file   (build_status.py)
  Step 2 – Render:  JSON file                →  HTML + CSV  (render.py)

Serialization converts the rich in-memory model objects (SourcePackage, SPPH,
BuildLog, …) into a plain JSON-serializable dict.  Deserialization reconstructs
lightweight proxy objects that expose exactly the same public interface as the
originals, so html_generator.py and csv_generator.py need zero changes.

JSON structure
--------------
{
  "meta": {
    "name":            str,           # output file prefix
    "generated":       str,           # "Started: … / Finished: …"
    "archive":         {"name": str, "displayname": str},
    "updates_archive": {"name": str, "displayname": str} | null,
    "main_archive":    {"name": str, "displayname": str} | null,
    "series":          {"name": str, "fullseriesname": str},
    "archs_by_archive": {"main": [...], "ports": [...]},
    "arch_list":       [...],
    "notice":          str | null,
    "release_only":    bool,
    "ref_series":      str | null
  },
  "packages": {
    "<pkg-name>": {
      "name":        str,
      "url":         str,
      "packagesets": [str, ...],
      "teams":       [str, ...],
      "tagged_bugs": [{"id": int, "title": str}, ...],
      "versions": [
        {
          "version":    str,
          "pocket":     str,
          "current":    bool | null,
          "changed_by": str | null,   # already formatted as "Name (login)"
          "logs": {
            "<arch>": {
              "buildstate": str,
              "url":        str,
              "log":        str,
              "tooltip":    str
            }
          }
        }
      ]
    }
  },
  "components":       {"main": [pkg-name, ...], "restricted": [...], ...},
  "packagesets_ftbfs": {"<ps-name>":   [pkg-name, ...], ...},
  "teams_ftbfs":       {"<team-name>": [pkg-name, ...], ...}
}
"""

from __future__ import annotations

import json
import os
from typing import Any

from lp_ftbfs_report.models import SourcePackage

# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _serialize_source_package(pkg: SourcePackage) -> dict:
    """Serialize a SourcePackage to a JSON-serializable dict."""
    versions = []
    for ver in pkg.versions:
        logs: dict[str, dict] = {}
        for arch, log in ver.logs.items():
            logs[arch] = {
                "buildstate": log.buildstate,
                "url": log.url,
                "log": log.log,
                "tooltip": log.tooltip,
            }
        versions.append(
            {
                "version": ver.version,
                "pocket": ver.pocket,
                "current": ver.current,
                # PersonTeam.__str__ already returns "Display Name (login)"
                "changed_by": str(ver.changed_by) if ver.changed_by else None,
                "logs": logs,
            }
        )

    return {
        "name": pkg.name,
        "url": pkg.url,
        "packagesets": sorted(pkg.packagesets),
        "teams": sorted(pkg.teams),
        "tagged_bugs": [{"id": bug.id, "title": bug.title} for bug in pkg.tagged_bugs],
        "versions": versions,
    }


def serialize_report(
    components: dict[str, list[SourcePackage]],
    packagesets_ftbfs: dict[str, list[SourcePackage]],
    teams_ftbfs: dict[str, list[SourcePackage]],
    meta: dict[str, Any],
) -> dict:
    """Serialize the complete report state to a JSON-serializable dict.

    All SourcePackage objects are deduplicated into a flat "packages" dict and
    referenced by name from "components", "packagesets_ftbfs", and "teams_ftbfs".

    Args:
        components: Dict mapping component name → list of SourcePackage
        packagesets_ftbfs: Dict mapping packageset name → list of SourcePackage
        teams_ftbfs: Dict mapping team name → list of SourcePackage
        meta: Metadata dict (archive info, series, arch_list, …)

    Returns:
        JSON-serializable dict.
    """
    # Deduplicate: collect all unique SourcePackage objects by name.
    all_packages: dict[str, SourcePackage] = {}
    for pkg_list in (
        list(components.values()) + list(packagesets_ftbfs.values()) + list(teams_ftbfs.values())
    ):
        for pkg in pkg_list:
            all_packages[pkg.name] = pkg

    return {
        "meta": meta,
        "packages": {name: _serialize_source_package(pkg) for name, pkg in all_packages.items()},
        "components": {
            comp: [pkg.name for pkg in pkg_list] for comp, pkg_list in components.items()
        },
        "packagesets_ftbfs": {
            ps: [pkg.name for pkg in pkg_list] for ps, pkg_list in packagesets_ftbfs.items()
        },
        "teams_ftbfs": {
            team: [pkg.name for pkg in pkg_list] for team, pkg_list in teams_ftbfs.items()
        },
    }


def write_json(data: dict, path: str) -> None:
    """Atomically write a report data dict to a JSON file."""
    tmp_path = path + ".new"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.rename(tmp_path, path)


def read_json(path: str) -> dict:
    """Read a report data dict from a JSON file."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Terminal output helpers (shared by build_status.py and render.py)
# ---------------------------------------------------------------------------

# Directory containing the bundled HTML assets (style.css, filters.js, ...).
# Both entry-point modules live in the same package directory, so __file__
# here resolves to the same path as it would from build_status.py or render.py.
_HTML_ASSET_DIR = os.path.join(os.path.dirname(__file__), "html")


def print_summary(
    message: str,
    paths: list[tuple[str, str]],
    out_dir: str | None = None,
) -> None:
    """Print a coloured summary of generated output files.

    Args:
        message: Header line, e.g. ``"Report generation complete!"``.
        paths: List of ``(label, path)`` tuples for each output file.
        out_dir: When given, also list each HTML asset copied into it.
    """
    GREEN = "\033[32m"
    CYAN = "\033[36m"
    BOLD = "\033[1m"
    RESET = "\033[0m"
    CHECK = "\u2714"

    print()
    print(f"{BOLD}{GREEN}{CHECK}  {message}{RESET}")
    for label, path in paths:
        print(f"   {CYAN}{label:<5}{RESET}  {path}")
    if out_dir is not None:
        for asset in sorted(os.listdir(_HTML_ASSET_DIR)):
            print(f"   {CYAN}ASSET{RESET}  {os.path.join(out_dir, asset)}")
    print()


# ---------------------------------------------------------------------------
# Lightweight proxy classes used during the render step
#
# These expose exactly the same public interface as the original model classes
# (SourcePackage, SPPH, BuildLog, PersonTeam, …) so that html_generator.py,
# csv_generator.py, and the Jinja2 template require no modifications.
# ---------------------------------------------------------------------------


class _BugProxy:
    """Minimal stand-in for a Launchpad bug object."""

    __slots__ = ("id", "title")

    def __init__(self, data: dict) -> None:
        self.id: int = data["id"]
        self.title: str = data["title"]


class _BuildLogProxy:
    """Stand-in for SPPH.BuildLog."""

    __slots__ = ("buildstate", "url", "log", "tooltip")

    def __init__(self, data: dict) -> None:
        self.buildstate: str = data["buildstate"]
        self.url: str = data["url"]
        self.log: str = data["log"]
        self.tooltip: str = data["tooltip"]


class _SPPHProxy:
    """Stand-in for SPPH (source package publishing history version)."""

    __slots__ = ("version", "pocket", "current", "_changed_by_str", "logs")

    def __init__(self, data: dict) -> None:
        self.version: str = data["version"]
        self.pocket: str = data["pocket"]
        self.current: bool | None = data.get("current")
        self._changed_by_str: str = data.get("changed_by") or "unknown"
        self.logs: dict[str, _BuildLogProxy] = {
            arch: _BuildLogProxy(log_data) for arch, log_data in data.get("logs", {}).items()
        }

    def getChangedBy(self) -> str:
        """Matches SPPH.getChangedBy() used by the Jinja2 template."""
        return f"Changed-By: {self._changed_by_str}"


class _SourcePackageProxy:
    """Stand-in for SourcePackage; implements the full rendering interface."""

    def __init__(self, data: dict) -> None:
        self.name: str = data["name"]
        self.url: str = data["url"]
        self.packagesets: set[str] = set(data.get("packagesets", []))
        self.teams: set[str] = set(data.get("teams", []))
        self.tagged_bugs: list[_BugProxy] = [_BugProxy(b) for b in data.get("tagged_bugs", [])]
        self.versions: list[_SPPHProxy] = [_SPPHProxy(v) for v in data.get("versions", [])]

    # --- Methods called by generate_page() / html_generator.py ---

    def isFTBFS(self, arch_list: list[str] | None = None, current: bool = True) -> bool:
        """Returns True if at least one FTBFS exists (mirrors SourcePackage.isFTBFS)."""
        for ver in self.versions:
            if ver.current != current:
                continue
            for arch in arch_list or []:
                if arch in ver.logs:
                    return True
        return False

    def getCount(self, arch: str, state: str) -> int:
        """Get count of builds with a specific state for an architecture."""
        return sum(
            1 for ver in self.versions if arch in ver.logs and ver.logs[arch].buildstate == state
        )

    def getPackagesets(self, name: str | None = None) -> list[str]:
        """Return the list of packagesets, optionally excluding one by name."""
        if name is None:
            return list(self.packagesets)
        return list(self.packagesets.difference({name}))


class _ArchiveProxy:
    """Stand-in for a Launchpad archive object."""

    __slots__ = ("name", "displayname")

    def __init__(self, data: dict) -> None:
        self.name: str = data["name"]
        self.displayname: str = data["displayname"]


class _SeriesProxy:
    """Stand-in for a Launchpad distro-series object."""

    __slots__ = ("name", "fullseriesname")

    def __init__(self, data: dict) -> None:
        self.name: str = data["name"]
        self.fullseriesname: str = data["fullseriesname"]


# ---------------------------------------------------------------------------
# Deserialization
# ---------------------------------------------------------------------------


def deserialize_report(
    data: dict,
) -> tuple[
    dict[str, list[Any]],
    dict[str, list[Any]],
    dict[str, list[Any]],
    dict[str, Any],
]:
    """Deserialize a report data dict into proxy objects ready for rendering.

    Args:
        data: Dict previously produced by serialize_report() / read_json().

    Returns:
        Tuple of ``(components, packagesets_ftbfs, teams_ftbfs, render_kwargs)``
        where ``render_kwargs`` contains all keyword arguments expected by
        ``generate_page()`` (except *name*, which is returned separately under
        the key ``"name"``).
    """
    meta = data["meta"]

    # Build the flat package-proxy registry.
    pkg_proxies: dict[str, _SourcePackageProxy] = {
        name: _SourcePackageProxy(pkg_data) for name, pkg_data in data.get("packages", {}).items()
    }

    def _resolve(names: list[str]) -> list[_SourcePackageProxy]:
        return [pkg_proxies[n] for n in names if n in pkg_proxies]

    components: dict[str, list[_SourcePackageProxy]] = {
        comp: _resolve(names) for comp, names in data.get("components", {}).items()
    }
    packagesets_ftbfs: dict[str, list[_SourcePackageProxy]] = {
        ps: _resolve(names) for ps, names in data.get("packagesets_ftbfs", {}).items()
    }
    teams_ftbfs: dict[str, list[_SourcePackageProxy]] = {
        team: _resolve(names) for team, names in data.get("teams_ftbfs", {}).items()
    }

    # Build proxy objects for archive / series so generate_page() can access
    # .displayname / .fullseriesname without knowing about the JSON format.
    archive = _ArchiveProxy(meta["archive"])
    updates_archive = (
        _ArchiveProxy(meta["updates_archive"]) if meta.get("updates_archive") else None
    )
    main_archive = _ArchiveProxy(meta["main_archive"]) if meta.get("main_archive") else None
    series = _SeriesProxy(meta["series"])

    render_kwargs: dict[str, Any] = {
        "name": meta["name"],
        "archive": archive,
        "updates_archive": updates_archive,
        "series": series,
        "archs_by_archive": meta.get("archs_by_archive", {"main": [], "ports": []}),
        "main_archive": main_archive,
        "arch_list": meta.get("arch_list", []),
        "notice": meta.get("notice"),
        "release_only": bool(meta.get("release_only", False)),
        "ref_series": meta.get("ref_series"),
        "generated": meta.get("generated", ""),
    }

    return components, packagesets_ftbfs, teams_ftbfs, render_kwargs
