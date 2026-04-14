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
    FetcherContext,
    SeriesInfo,
    translate_api_web,
)
from lp_ftbfs_report.fetchers.dummy import DummyFetcher
from lp_ftbfs_report.fetchers.ppa import PPAFetcher, parse_ppa_spec
from lp_ftbfs_report.fetchers.test_rebuild import TestRebuildFetcher

__all__ = [
    "BaseFetcher",
    "BuildRecord",
    "ArchiveInfo",
    "SeriesInfo",
    "FetcherContext",
    "TestRebuildFetcher",
    "PPAFetcher",
    "DummyFetcher",
    "parse_ppa_spec",
    "translate_api_web",
]
