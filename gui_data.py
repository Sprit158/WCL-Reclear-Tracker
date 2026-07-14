from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from schedule_database import connect_schedule_db
from settings_manager import get_guild_profile_from_settings


@dataclass(frozen=True)
class TableColumn:
    key: str
    title: str
    width: int
    numeric: bool = False


TABLE_COLUMNS: tuple[TableColumn, ...] = (
    TableColumn("rank", "Rank", 66, True),
    TableColumn("guild", "Guild", 190),
    TableColumn("realm", "Realm", 116),
    TableColumn("region", "Region", 70),
    TableColumn("two_day", "2D?", 58),
    TableColumn("average", "Avg", 68, True),
    TableColumn("first_month", "M1", 68, True),
    TableColumn("median", "Med", 68, True),
    TableColumn("hours", "Hrs", 68, True),
    TableColumn("weeks", "Wks", 60, True),
    TableColumn("nights", "Nights", 76, True),
    TableColumn("reports", "Reports", 80, True),
    TableColumn("days", "Common days", 156),
    TableColumn("confidence", "Status", 100),
)


NUMERIC_FILTERS = ("rank", "average", "first_month", "median", "hours", "weeks", "nights", "reports")


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_schedule_rows(config: dict[str, Any]) -> list[dict[str, Any]]:
    conn = connect_schedule_db(config)
    profile = get_guild_profile_from_settings()
    try:
        records = conn.execute(
            """
            SELECT rank, guild, realm, region, inferred_days_per_week,
                   average_raid_days_per_active_week, first_month_average_raid_days,
                   logged_window_hours_per_week, active_weeks, raid_nights_found,
                   reports_found, inferred_raid_days, is_likely_two_day,
                   schedule_confidence, reason, schedule_source, scanned_at_unix
            FROM schedule_scan_results
            ORDER BY rank IS NULL, rank, guild COLLATE NOCASE
            """
        ).fetchall()
    finally:
        conn.close()

    rows: list[dict[str, Any]] = []
    for record in records:
        row = dict(record)
        row.update(
            {
                "average": _number(row.pop("average_raid_days_per_active_week")),
                "first_month": _number(row.pop("first_month_average_raid_days")),
                "median": _number(row.pop("inferred_days_per_week")),
                "hours": _number(row.pop("logged_window_hours_per_week")),
                "weeks": _number(row.pop("active_weeks")),
                "nights": _number(row.pop("raid_nights_found")),
                "reports": _number(row.pop("reports_found")),
                "days": row.pop("inferred_raid_days") or "",
                "confidence": (row.pop("schedule_confidence") or "unverified").lower(),
            }
        )
        confidence = row["confidence"]
        row["two_day"] = "?" if confidence in {"unverified", "error"} else ("Yes" if row.pop("is_likely_two_day") else "No")
        if "is_likely_two_day" in row:
            row.pop("is_likely_two_day")
        row["is_own"] = bool(
            profile
            and row["guild"].strip().casefold() == profile.name.strip().casefold()
            and row["realm"].strip().casefold() == profile.realm.strip().casefold()
            and row["region"].strip().upper() == profile.region.strip().upper()
        )
        rows.append(row)
    return rows


def _matches_text(value: Any, wanted: str) -> bool:
    return not wanted or wanted.casefold() in str(value or "").casefold()


def _matches_range(value: Any, minimum: str, maximum: str) -> bool:
    number = _number(value)
    if minimum.strip():
        try:
            if number is None or number < float(minimum):
                return False
        except ValueError:
            return False
    if maximum.strip():
        try:
            if number is None or number > float(maximum):
                return False
        except ValueError:
            return False
    return True


def filter_schedule_rows(rows: list[dict[str, Any]], filters: dict[str, str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        if not _matches_text(row["guild"], filters.get("guild", "")):
            continue
        if not _matches_text(row["realm"], filters.get("realm", "")):
            continue
        if not _matches_text(row["region"], filters.get("region", "")):
            continue
        if not _matches_text(row["days"], filters.get("days", "")):
            continue
        if filters.get("two_day", "All") not in {"", "All"} and row["two_day"] != filters["two_day"]:
            continue
        if filters.get("confidence", "All") not in {"", "All"} and row["confidence"] != filters["confidence"].casefold():
            continue
        if filters.get("own_only", "Off") == "On" and not row["is_own"]:
            continue
        if any(
            not _matches_range(row[key], filters.get(f"{key}_min", ""), filters.get(f"{key}_max", ""))
            for key in NUMERIC_FILTERS
        ):
            continue
        result.append(row)
    return result


def summary_for(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "guilds": len(rows),
        "two_day": sum(row["two_day"] == "Yes" for row in rows),
        "verified": sum(row["confidence"] not in {"unverified", "error"} for row in rows),
        "attention": sum(row["confidence"] in {"unverified", "error"} for row in rows),
    }
