"""Tests for the Progress tracker and BuildRecordSet."""

from __future__ import annotations

import io

from lp_ftbfs_report.fetchers.base import BuildRecord, BuildRecordSet
from lp_ftbfs_report.progress import Progress


def _record(name: str) -> BuildRecord:
    return BuildRecord(
        source_package_name=name,
        source_package_version="1.0",
        arch_tag="amd64",
        buildstate="Failed to build",
        datebuilt=None,
        current_source_publication_link="link",
        build_log_url=None,
        upload_log_url=None,
        dependencies=None,
        self_link="self",
    )


# --------------------------------------------------------------------------- #
# BuildRecordSet
# --------------------------------------------------------------------------- #


def test_build_record_set_iterates_and_counts():
    """A BuildRecordSet is iterable and exposes its total via len()."""

    def factory(on_item):
        for name in ("a", "b", "c"):
            if on_item:
                on_item(None)
            yield _record(name)

    records = BuildRecordSet(factory, total=3)
    assert len(records) == 3

    seen = [r.source_package_name for r in records]
    assert seen == ["a", "b", "c"]


def test_build_record_set_on_item_ticks():
    """The injected on_item callback is invoked once per source record."""

    ticks: list[str | None] = []

    def factory(on_item):
        # yielded record
        if on_item:
            on_item(None)
        yield _record("a")
        # filtered record (not yielded)
        if on_item:
            on_item("filtered")

    records = BuildRecordSet(factory, total=2)
    records.on_item = lambda cat=None: ticks.append(cat)

    consumed = list(records)
    assert len(consumed) == 1
    # One plain tick for the yielded record, one "filtered" for the skipped one.
    assert ticks == [None, "filtered"]


def test_build_record_set_total_unknown():
    """total=None yields len() == 0 and an unknown-count progress line."""

    def factory(on_item):
        if on_item:
            on_item(None)
        yield _record("a")

    records = BuildRecordSet(factory, total=None)
    assert len(records) == 0


# --------------------------------------------------------------------------- #
# Progress
# --------------------------------------------------------------------------- #


def test_progress_non_tty_renders_start_and_finish():
    """On a non-TTY stream, finish() renders a 100% line with categories."""
    stream = io.StringIO()
    progress = Progress(4, "Failed to build", stream=stream, state_index=1, state_count=1)
    # Non-TTY: _start prints an initial 0/4 line.
    lines = stream.getvalue().splitlines()
    assert lines == ["[1/1] Failed to build: 0/4 (0%)  kept=0  skipped=0"]

    progress.tick()  # yielded, categorized later
    progress.mark("kept")
    progress.tick("filtered")  # filtered record
    progress.tick()
    progress.mark("kept")
    progress.mark("never-built")
    progress.tick()
    progress.mark("kept")
    progress.tick()
    progress.mark("kept")

    progress.finish()
    final = stream.getvalue().splitlines()[-1]
    assert "4/4 (100%)" in final
    assert "kept=4" in final
    assert "never-built=1" in final
    assert "filtered=1" in final


def test_progress_empty_batch_single_line():
    """An empty batch prints one start line and no duplicate finish line."""
    stream = io.StringIO()
    progress = Progress(0, "Chroot problem", stream=stream, state_index=3, state_count=5)
    progress.finish()
    lines = stream.getvalue().splitlines()
    assert lines == ["[3/5] Chroot problem: 0 processed  kept=0  skipped=0"]


def test_progress_verbose_emits_summary_only():
    """In verbose mode no live bar is drawn; finish() prints a summary line."""
    stream = io.StringIO()
    progress = Progress(
        2, "Failed to build", stream=stream, verbose=True, state_index=1, state_count=1
    )
    progress.tick()
    progress.mark("kept")
    progress.tick()
    progress.mark("kept")
    progress.mark("never-built")
    progress.finish()

    lines = stream.getvalue().splitlines()
    # Header then summary.
    assert lines[0] == "[1/1] Failed to build: 2 records"
    assert lines[-1] == "[1/1] Failed to build: done  kept=2  skipped=0  never-built=1"
