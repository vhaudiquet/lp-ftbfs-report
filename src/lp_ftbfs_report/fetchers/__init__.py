"""Fetcher implementations for FTBFS report generator.

This package provides different data fetcher implementations:
- TestRebuildFetcher: For Ubuntu test rebuild archives
- PPAFetcher: For Personal Package Archives
- DummyFetcher: For testing with static JSON data
"""

from lp_ftbfs_report.fetchers.base import (
    ArchiveInfo,
    BaseFetcher,
    BuildRecord,
    BuildRecordSet,
    SeriesInfo,
)
from lp_ftbfs_report.fetchers.dummy import DummyFetcher
from lp_ftbfs_report.fetchers.ppa import PPAFetcher, parse_ppa_spec
from lp_ftbfs_report.fetchers.test_rebuild import TestRebuildFetcher

__all__ = [
    "BaseFetcher",
    "BuildRecord",
    "BuildRecordSet",
    "ArchiveInfo",
    "SeriesInfo",
    "TestRebuildFetcher",
    "PPAFetcher",
    "DummyFetcher",
    "parse_ppa_spec",
]
