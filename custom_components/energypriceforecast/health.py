"""How whole and how fresh the data is, for diagnostics and attributes.

Two questions a bug report keeps asking, answered without Home Assistant:

* Is a series continuous? Every slot should end where the next one starts.
  A missing hour - as around a daylight-saving change - or two slots for the
  same time shows up here as a gap or an overlap, with the times involved.
* Is each source fresh? The coordinator keeps the last good data of a
  source when one of its requests fails, which is right, but it hides that
  the retail price may be an hour old while the summary is current. The
  tracker records per source when it last succeeded and what last failed.

Neither raises a repair issue: the user cannot fix either, so they belong in
the diagnostics and in the attributes of the series they describe.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# Enough to see the pattern of a problem; a broken feed would otherwise fill
# the diagnostics with hundreds of identical lines.
MAX_LISTED = 5
MAX_ERROR_LENGTH = 200

# aiohttp puts the request URL into its messages, and the query carries the
# postal code. The diagnostics redact it from the config, so the error text
# must not bring it back.
_QUERY = re.compile(r"\?[^\s'\")]*")


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def series_continuity(entries: Any) -> dict[str, Any]:
    """Describe the gaps and overlaps of one series of {start, end} slots.

    Slots are compared in UTC, so the 25-hour and 23-hour days of a
    daylight-saving change need no special case: a correct series has 25 or
    23 hourly slots that join up, and a series that lost or doubled the
    repeated hour shows a gap or an overlap there.
    """
    if not isinstance(entries, list):
        return {"slots": 0, "malformed": 0, "gaps": 0, "overlaps": 0}

    slots: list[tuple[datetime, datetime]] = []
    malformed = 0
    for entry in entries:
        start = _parse(entry.get("start")) if isinstance(entry, dict) else None
        end = _parse(entry.get("end")) if isinstance(entry, dict) else None
        if start is None or end is None or end <= start:
            malformed += 1
            continue
        slots.append((start, end))
    slots.sort()

    gaps: list[dict[str, Any]] = []
    overlaps: list[dict[str, Any]] = []
    for (start, end), (next_start, next_end) in zip(slots, slots[1:]):
        if next_start > end:
            gaps.append(
                {
                    "after": _iso(end),
                    "before": _iso(next_start),
                    "missing_minutes": int((next_start - end).total_seconds() // 60),
                }
            )
        elif next_start < end:
            overlaps.append(
                {
                    "slot": _iso(start),
                    "next_slot": _iso(next_start),
                    "overlap_minutes": int(
                        (min(end, next_end) - next_start).total_seconds() // 60
                    ),
                }
            )

    report: dict[str, Any] = {
        "slots": len(slots),
        "malformed": malformed,
        "gaps": len(gaps),
        "overlaps": len(overlaps),
    }
    if slots:
        report["first_start"] = _iso(slots[0][0])
        report["last_end"] = _iso(slots[-1][1])
    if gaps:
        report["gap_details"] = gaps[:MAX_LISTED]
    if overlaps:
        report["overlap_details"] = overlaps[:MAX_LISTED]
    return report


def short_error(err: BaseException | str) -> str:
    """One line of what went wrong, without a query string or a novel."""
    text = str(err)
    if not text and isinstance(err, BaseException):
        text = type(err).__name__
    text = " ".join(_QUERY.sub("?…", text).split())
    if len(text) > MAX_ERROR_LENGTH:
        text = text[: MAX_ERROR_LENGTH - 1] + "…"
    return text


@dataclass
class _SourceState:
    last_attempt: datetime | None = None
    last_success: datetime | None = None
    last_failure: datetime | None = None
    last_error: str | None = None


@dataclass
class SourceHealth:
    """When each data source last answered, and what last went wrong."""

    _sources: dict[str, _SourceState] = field(default_factory=dict)

    def success(self, source: str, now: datetime) -> None:
        state = self._sources.setdefault(source, _SourceState())
        state.last_attempt = now
        state.last_success = now

    def failure(self, source: str, now: datetime, err: BaseException | str) -> None:
        state = self._sources.setdefault(source, _SourceState())
        state.last_attempt = now
        state.last_failure = now
        state.last_error = short_error(err)

    def status(self, source: str) -> str | None:
        """fresh, stale (older data kept after a failure) or missing.

        None for a source this entry never asked for, such as the retail
        estimate when retail pricing is off.
        """
        state = self._sources.get(source)
        if state is None or state.last_attempt is None:
            return None
        if state.last_success is None:
            return "missing"
        return "fresh" if state.last_success == state.last_attempt else "stale"

    def last_success(self, source: str) -> datetime | None:
        state = self._sources.get(source)
        return state.last_success if state else None

    def snapshot(self, now: datetime) -> dict[str, dict[str, Any]]:
        """Every source this entry asked for, ready for the diagnostics."""
        report: dict[str, dict[str, Any]] = {}
        for source, state in sorted(self._sources.items()):
            age = (
                int((now - state.last_success).total_seconds() // 60)
                if state.last_success is not None
                else None
            )
            report[source] = {
                "status": self.status(source),
                "last_success": _iso(state.last_success) if state.last_success else None,
                "age_minutes": age,
                "last_failure": _iso(state.last_failure) if state.last_failure else None,
                "last_error": state.last_error,
            }
        return report
