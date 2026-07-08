#!/usr/bin/python3

"""Progress reporting for FTBFS report generation.

The :class:`Progress` helper tracks how many build records have been processed
for a given build state and renders a single, continuously-updating line.

* On a TTY it redraws the line in place using a carriage return so the user
  sees a live ``current/total (pct%)`` bar.
* On a non-TTY (piped logs, cron, snap log capture) it prints a compact
  summary line periodically so the captured output stays readable instead of
  growing to thousands of lines.

Per-build detail (the ``never built before`` / ``Find reference build`` /
``{datebuilt} {title}`` lines) is only emitted when ``verbose=True``.

The tracker is intentionally dependency-free.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from typing import IO

# Categories tracked per processed build record. ``filtered`` covers records
# consumed from the source collection but not yielded for processing (e.g. a
# build for an architecture that was not requested, or a build log for an
# already-superseded version). ``skipped`` covers yielded records that the
# processing loop discarded (e.g. a build that succeeded in the updates
# archive). ``kept`` covers yielded records added to the report. ``kept``,
# ``skipped`` and ``filtered`` are mutually exclusive and together account for
# every record pulled from the collection, so their sum equals ``current``.
# ``never-built`` is a subset of ``kept``: records that were added to the
# report but had no successful build in the reference series.
_CATEGORIES: tuple[str, ...] = ("kept", "skipped", "never-built", "filtered")

# A tick callback invoked once per record pulled from the source collection.
# It receives an optional category name: ``None`` for a plain tick (the record
# was yielded and will be categorized later by the processing loop) or one of
# the category names above (for records filtered out before yielding).
TickFn = Callable[[str | None], None]


class Progress:
    """Single-line progress tracker for build record processing."""

    def __init__(
        self,
        total: int | None,
        label: str,
        *,
        verbose: bool = False,
        stream: IO[str] | None = None,
        state_index: int | None = None,
        state_count: int | None = None,
        interval: float = 2.0,
    ) -> None:
        """Initialize the progress tracker.

        Args:
            total: Total number of records to process, or ``None`` if the
                total is unknown (in which case only a running count is shown).
            label: Human-readable label for the batch (e.g. the build state).
            verbose: When True, per-build detail is expected to be printed by
                callers and no live bar is drawn.
            stream: Output stream (defaults to stderr).
            state_index: 1-based index of this state within its phase.
            state_count: Number of states in this phase.
            interval: Minimum seconds between non-TTY summary prints.
        """
        self.total = total
        self.label = label
        self.verbose = verbose
        self.stream = stream if stream is not None else sys.stderr
        self.state_index = state_index
        self.state_count = state_count
        self._interval = interval

        self.current = 0
        self.counts: dict[str, int] = dict.fromkeys(_CATEGORIES, 0)

        self._last_print: float = 0.0
        self._is_tty = bool(getattr(self.stream, "isatty", lambda: False)())
        self._prefix = ""
        if state_index is not None and state_count is not None:
            self._prefix = f"[{state_index}/{state_count}] "

        self._start()

    # -- public API -------------------------------------------------------

    def tick(self, category: str | None = None) -> None:
        """Advance the counter by one record pulled from the source.

        Args:
            category: Optional category to increment in addition to the
                running counter. Use ``"filtered"`` for records that are
                consumed but not yielded for processing. ``None`` (the default)
                ticks the running counter only; the caller is expected to
                categorize the record later via :meth:`mark`.
        """
        self.current += 1
        if category is not None:
            self.counts[category] += 1
        if not self.verbose:
            self._render()

    def mark(self, category: str) -> None:
        """Categorize a yielded record without advancing the running counter.

        The running counter is driven entirely by :meth:`tick` (called once
        per source record). This method assigns a processed record to one of
        the non-``filtered`` categories.
        """
        self.counts[category] += 1

    def finish(self) -> None:
        """Finalize the tracker, ensuring a 100%% summary is rendered."""
        if self.verbose:
            print(self._summary_line(), file=self.stream, flush=True)
            return
        # Snap to 100% so the final line reflects completion even if some
        # records were filtered out without an explicit tick.
        if self.total:
            self.current = self.total
        # For empty batches on a non-TTY, the start line already said all
        # there is to say; avoid a duplicate.
        if not self._is_tty and not self.total and not self.current:
            return
        if self._is_tty:
            self._render(force=True)
            print(file=self.stream)  # newline after the bar
        else:
            self._render(force=True)

    # -- rendering --------------------------------------------------------

    def _start(self) -> None:
        if self.verbose:
            count = f"{self.total} records" if self.total is not None else "unknown count"
            print(f"{self._prefix}{self.label}: {count}", file=self.stream, flush=True)
            return
        if self._is_tty:
            self._render(force=True)
        else:
            print(self._count_line(), file=self.stream, flush=True)
            self._last_print = time.monotonic()

    def _render(self, *, force: bool = False) -> None:
        if self._is_tty:
            print(f"\r{self._count_line()}", end="", file=self.stream, flush=True)
            return
        now = time.monotonic()
        if force or now - self._last_print >= self._interval:
            print(self._count_line(), file=self.stream, flush=True)
            self._last_print = now

    def _count_line(self) -> str:
        if self.total:
            pct = self.current / self.total * 100
            progress = f"{self.current}/{self.total} ({pct:.0f}%)"
        else:
            progress = f"{self.current} processed"
        return f"{self._prefix}{self.label}: {progress}  {self._categories_str()}"

    def _summary_line(self) -> str:
        return f"{self._prefix}{self.label}: done  {self._categories_str()}"

    def _categories_str(self) -> str:
        parts = [f"kept={self.counts['kept']}", f"skipped={self.counts['skipped']}"]
        # never-built is a subset of kept; show it only when non-zero so the
        # common case stays compact.
        if self.counts["never-built"]:
            parts.append(f"never-built={self.counts['never-built']}")
        if self.counts["filtered"]:
            parts.append(f"filtered={self.counts['filtered']}")
        return "  ".join(parts)
