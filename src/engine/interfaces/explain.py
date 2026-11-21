"""Render the explain command as a readable terminal walkthrough."""

from __future__ import annotations

import os
import re
import shutil
import textwrap
from pathlib import Path
from typing import TextIO

_PHASE_PATTERN = re.compile(r"^\[(?P<number>\d+)/(?P<total>\d+)] (?P<title>.+)$")
_EVENT_PATTERN = re.compile(
    r"^  event=(?P<event_id>\S+) records=(?P<records>.+?) "
    r"classification=(?P<classification>\S+) state=(?P<state>\S+)$"
)
_EVENT_TOTAL_PATTERN = re.compile(r"(?P<count>\d+) logical event\(s\)")

_PHASE_LABELS = {
    1: "LOAD + VALIDATE",
    2: "NORMALIZE + CLASSIFY",
    3: "ACCOUNT",
    4: "WRITE REPORTS",
}
_PHASE_COLORS = {
    1: "36",  # cyan
    2: "34",  # blue
    3: "35",  # magenta
    4: "32",  # green
}


class ExplainRenderer:
    """Turn the pipeline's stable trace messages into a terminal-friendly layout."""

    def __init__(
        self,
        *,
        fixtures_dir: Path,
        output_dir: Path,
        stream: TextIO,
        use_color: bool | None = None,
    ) -> None:
        self._fixtures_dir = fixtures_dir
        self._output_dir = output_dir
        self._stream = stream
        self._use_color = self._detect_color() if use_color is None else use_color
        terminal_width = shutil.get_terminal_size(fallback=(88, 24)).columns
        self._width = max(72, min(terminal_width, 100))
        self._event_count = 0
        self._event_total: int | None = None

    def _detect_color(self) -> bool:
        is_terminal = bool(getattr(self._stream, "isatty", lambda: False)())
        return is_terminal and "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb"

    def _style(self, text: str, *codes: str) -> str:
        if not self._use_color:
            return text
        return f"\033[{';'.join(codes)}m{text}\033[0m"

    def _write(self, text: str = "") -> None:
        print(text, file=self._stream)

    def _box_line(self, text: str, *, color: str = "36") -> None:
        content_width = self._width - 4
        for line in textwrap.wrap(text, width=content_width) or [""]:
            padded = f"│ {line:<{content_width}} │"
            self._write(self._style(padded, color))

    def _path_line(self, label: str, path: Path) -> None:
        prefix = f"  {label:<9}"
        subsequent = " " * len(prefix)
        lines = textwrap.wrap(
            str(path),
            width=max(20, self._width - len(prefix)),
            subsequent_indent=subsequent,
        ) or [""]
        self._write(prefix + lines[0])
        for line in lines[1:]:
            self._write(line)

    def start(self) -> None:
        """Print run context before the pipeline starts."""
        border = f"╭{'─' * (self._width - 2)}╮"
        footer = f"╰{'─' * (self._width - 2)}╯"
        self._write(self._style(border, "36", "1"))
        self._box_line("WALLET TAX ENGINE · EXPLAIN", color="36")
        self._box_line(
            "Evidence → normalization → tax-lot accounting → reports", color="36"
        )
        self._write(self._style(footer, "36", "1"))
        self._path_line("Fixtures", self._fixtures_dir)
        self._path_line("Reports", self._output_dir)

    def _render_phase(self, *, number: int, total: int, title: str) -> None:
        color = _PHASE_COLORS.get(number, "36")
        label = f"{number:02d}/{total:02d} · {_PHASE_LABELS.get(number, title.upper())}"
        fill = "─" * max(1, self._width - len(label) - 5)
        self._write()
        self._write(self._style(f"╭─ {label} {fill}╮", color, "1"))
        self._box_line(title, color=color)
        self._write(self._style(f"╰{'─' * (self._width - 2)}╯", color, "1"))

    def _render_stage_detail(self, message: str) -> None:
        operation, separator, result = message.strip().partition(" -> ")
        if not separator:
            self._write(f"  {message.strip()}")
            return
        self._write(f"  {self._style('✓', '32', '1')} {self._style(operation, '1')}")
        if operation == "process_evidence()":
            event_summary, _, coverage = result.partition(", coverage=")
            self._write(f"    {event_summary}")
            coverage_items = re.findall(r"'([^']+)': (\d+)", coverage)
            if coverage_items:
                coverage_text = "  ·  ".join(
                    f"{status.replace('_', ' ')} {count}" for status, count in coverage_items
                )
                self._write(f"    coverage  {coverage_text}")
            return
        result = result.replace(", ", "  ·  ")
        lines = textwrap.wrap(result, width=self._width - 4) or [""]
        for line in lines:
            self._write(f"    {line}")

    def _render_event(
        self,
        *,
        event_id: str,
        records: str,
        classification: str,
        state: str,
    ) -> None:
        self._event_count += 1
        if self._event_total is None:
            position = str(self._event_count)
        else:
            digits = len(str(self._event_total))
            position = f"{self._event_count:0{digits}d}/{self._event_total}"
        state_color = "32" if state == "resolved" else "33"
        heading = (
            f"  {self._style(position, '2')}  "
            f"{self._style(classification.upper(), '1')} · "
            f"{self._style(state.upper(), state_color, '1')}"
        )
        self._write(heading)
        detail_prefix = " " * (len(position) + 4)
        source = f"{event_id}  ←  {records}"
        lines = textwrap.wrap(
            source,
            width=max(20, self._width - len(detail_prefix)),
            subsequent_indent=detail_prefix,
            break_long_words=False,
            break_on_hyphens=False,
        ) or [""]
        self._write(detail_prefix + self._style(lines[0], "2"))
        for line in lines[1:]:
            self._write(self._style(line, "2"))

    def _render_event_detail(self, message: str) -> None:
        stripped = message.strip()
        is_nested = len(message) - len(message.lstrip()) > 4
        marker = "•" if is_nested else "↳"
        indent = "           " if is_nested else "         "
        display = stripped.replace(" -> ", " → ")
        color = "33" if any(
            word in display.lower()
            for word in ("blocked", "unknown basis", "requires review", "quarantined")
        ) else None
        marker_text = self._style(marker, color or "35", "1")
        lines = textwrap.wrap(
            display,
            width=max(20, self._width - len(indent) - 2),
            subsequent_indent=" " * (len(indent) + 2),
            break_long_words=False,
            break_on_hyphens=False,
        ) or [""]
        first_line = self._style(lines[0], color) if color else lines[0]
        self._write(f"{indent}{marker_text} {first_line}")
        for line in lines[1:]:
            self._write(self._style(line, color) if color else line)

    def trace(self, message: str) -> None:
        """Render one trace message from the core pipeline."""
        phase_match = _PHASE_PATTERN.match(message)
        if phase_match:
            self._render_phase(
                number=int(phase_match.group("number")),
                total=int(phase_match.group("total")),
                title=phase_match.group("title"),
            )
            return

        event_total_match = _EVENT_TOTAL_PATTERN.search(message)
        if event_total_match:
            self._event_total = int(event_total_match.group("count"))

        event_match = _EVENT_PATTERN.match(message)
        if event_match:
            self._render_event(**event_match.groupdict())
            return

        if message.startswith("    "):
            self._render_event_detail(message)
            return
        self._render_stage_detail(message)

    def complete(
        self,
        *,
        disposal_count: int,
        income_count: int,
        exception_count: int,
        reconciliation_passed: bool,
    ) -> None:
        """Close the walkthrough with the report totals and invariant status."""
        color = "32" if reconciliation_passed else "31"
        label = "RUN COMPLETE" if reconciliation_passed else "RUN FAILED"
        fill = "─" * max(1, self._width - len(label) - 5)
        self._write()
        self._write(self._style(f"╭─ {label} {fill}╮", color, "1"))
        self._box_line(
            f"{disposal_count} disposal rows  ·  {income_count} income rows  ·  "
            f"{exception_count} exceptions",
            color=color,
        )
        status = "PASSED" if reconciliation_passed else "FAILED"
        self._box_line(f"Reconciliation  {status}", color=color)
        self._box_line(f"Reports         {self._output_dir}", color=color)
        self._write(self._style(f"╰{'─' * (self._width - 2)}╯", color, "1"))
