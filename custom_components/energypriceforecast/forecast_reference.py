"""Keep the first price forecast observed for each future slot."""

from __future__ import annotations

from datetime import datetime, timedelta, tzinfo
from math import isfinite
from typing import Any


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def update_reference(
    previous: dict[str, Any] | None,
    fresh: dict[str, Any],
    *,
    now: datetime,
    zone: tzinfo,
    basis: str,
    resolution: str,
) -> dict[str, Any]:
    """Retain today's and upcoming snapshots, never backfill a past forecast.

    A slot first seen as an official price gets a marker without a value.
    Even if the API later falls back to a forecast, that marker prevents
    presenting a post-publication prediction as an earlier reference.
    """
    identity = [fresh.get("country"), fresh.get("unit"), resolution, basis]
    previous = previous if isinstance(previous, dict) else {}
    old_slots = previous.get("slots", {}) if previous.get("identity") == identity else {}
    if not isinstance(old_slots, dict):
        old_slots = {}
    today = now.astimezone(zone).date()
    latest = now + timedelta(days=6)

    def boundaries(slot: Any) -> tuple[datetime, datetime] | None:
        if not isinstance(slot, dict):
            return None
        start = _timestamp(slot.get("start"))
        end = _timestamp(slot.get("end"))
        if start is None or end is None or not start < end:
            return None
        if start.astimezone(zone).date() < today or end > latest:
            return None
        return start, end

    slots = {
        key: dict(slot)
        for key, slot in old_slots.items()
        if boundaries(slot) is not None
    }
    entries = fresh.get("entries")
    for entry in entries if isinstance(entries, list) else []:
        bounds = boundaries(entry)
        if bounds is None:
            continue
        start, end = bounds
        key = f"{start.timestamp()}|{end.timestamp()}"
        if key in slots:
            continue
        value = entry.get("value")
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(value)
        ):
            continue
        source = entry.get("source")
        slot = {"start": entry["start"], "end": entry["end"]}
        if source == "forecast":
            if start <= now:
                continue
            slot.update(value=value, captured_at=now.isoformat())
        elif source not in (None, "day_ahead"):
            continue
        slots[key] = slot
    return {"identity": identity, "slots": slots}


def reference_entries(cache: dict[str, Any]) -> list[dict[str, Any]]:
    """Expose snapshots, not the internal official-price markers."""
    return sorted(
        (dict(slot) for slot in cache["slots"].values() if "captured_at" in slot),
        key=lambda slot: _timestamp(slot["start"]).timestamp(),
    )
