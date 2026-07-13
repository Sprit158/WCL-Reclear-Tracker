from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo
import json
import re

from settings_manager import get_global_cache_dir


type JsonDict = dict[str, Any]


@dataclass(slots=True)
class ChosenReport:
    date: str
    code: str
    title: str
    start_ms: int
    end_ms: int
    mythic_pull_count: int
    zone_id: int | None
    zone_name: str


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_") or "unknown"


def parse_date_to_ms(date_text: str, tz_name: str, end_of_day: bool = False) -> int:
    dt_date = datetime.strptime(date_text, "%Y-%m-%d").date()
    dt_time = time.max if end_of_day else time.min
    local = datetime.combine(dt_date, dt_time, tzinfo=ZoneInfo(tz_name))
    return int(local.timestamp() * 1000)


def season_range_ms(season_config: JsonDict) -> tuple[int, int]:
    tz_name = season_config.get("timezone", "Europe/London")
    start_date = season_config.get("start_date", "2026-01-01")
    end_date = season_config.get("end_date") or datetime.now(ZoneInfo(tz_name)).date().isoformat()

    return (
        parse_date_to_ms(start_date, tz_name, end_of_day=False),
        parse_date_to_ms(end_date, tz_name, end_of_day=True),
    )


def report_date(report: JsonDict, tz_name: str) -> str:
    start_ms = int(report.get("start", report.get("startTime", 0)))
    return datetime.fromtimestamp(start_ms / 1000, tz=ZoneInfo(tz_name)).date().isoformat()


def report_code(report: JsonDict) -> str:
    return report.get("id") or report.get("code") or ""


def report_zone_id(report: JsonDict) -> int | None:
    zone = report.get("zone")
    if isinstance(zone, int):
        return zone
    if isinstance(zone, dict):
        zid = zone.get("id")
        return int(zid) if zid is not None else None

    for key in ["zoneID", "zoneId", "zone_id"]:
        if report.get(key) is not None:
            return int(report[key])

    return None


def report_zone_name(report: JsonDict, zone_lookup: dict[int, str]) -> str:
    zone = report.get("zone")
    if isinstance(zone, dict):
        return zone.get("name", "")

    zid = report_zone_id(report)
    if zid is not None:
        return zone_lookup.get(zid, "")

    return ""


def load_or_fetch_guild_reports(
    client,
    guild_name: str,
    realm: str,
    region: str,
    start_ms: int,
    end_ms: int,
    force_refresh: bool,
) -> tuple[list[JsonDict], str]:
    folder = get_global_cache_dir("guild_reports")
    folder.mkdir(parents=True, exist_ok=True)

    filename = (
        f"{slugify(region)}_{slugify(realm)}_{slugify(guild_name)}_"
        f"{start_ms}_{end_ms}.json"
    )
    path = folder / filename

    if path.exists() and not force_refresh:
        wrapper = json.loads(path.read_text(encoding="utf-8"))
        return wrapper["data"], "cache"

    reports = client.fetch_guild_reports(
        guild_name=guild_name,
        realm=realm,
        region=region,
        start_ms=start_ms,
        end_ms=end_ms,
    )

    wrapper = {
        "schema_version": 1,
        "source": "warcraftlogs",
        "cache_type": "guild_reports",
        "guild": guild_name,
        "realm": realm,
        "region": region,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "data": reports,
    }

    path.write_text(json.dumps(wrapper, indent=2, ensure_ascii=False), encoding="utf-8")
    return reports, "api"


def load_or_fetch_zones(client, force_refresh: bool) -> tuple[list[JsonDict], str]:
    folder = get_global_cache_dir("zones")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "zones.json"

    if path.exists() and not force_refresh:
        wrapper = json.loads(path.read_text(encoding="utf-8"))
        return wrapper["data"], "cache"

    zones = client.fetch_zones()
    wrapper = {
        "schema_version": 1,
        "source": "warcraftlogs",
        "cache_type": "zones",
        "data": zones,
    }
    path.write_text(json.dumps(wrapper, indent=2, ensure_ascii=False), encoding="utf-8")
    return zones, "api"


def build_zone_lookup(zones: list[JsonDict]) -> dict[int, str]:
    lookup = {}
    for zone in zones:
        zid = zone.get("id")
        name = zone.get("name", "")
        if zid is not None:
            lookup[int(zid)] = name
    return lookup


def resolve_midnight_zone_ids(
    zones: list[JsonDict],
    configured_ids: list[int],
    name_contains: list[str],
) -> set[int]:
    ids = {int(zid) for zid in configured_ids if zid is not None}

    lowered_needles = [needle.lower() for needle in name_contains if needle]
    for zone in zones:
        zid = zone.get("id")
        name = str(zone.get("name", ""))
        if zid is None:
            continue

        if any(needle in name.lower() for needle in lowered_needles):
            ids.add(int(zid))

    return ids


def report_matches_midnight_zone(
    report: JsonDict,
    midnight_zone_ids: set[int],
    name_contains: list[str],
    zone_lookup: dict[int, str],
) -> bool:
    zid = report_zone_id(report)
    if zid is not None and zid in midnight_zone_ids:
        return True

    zname = report_zone_name(report, zone_lookup)
    if zname:
        return any(needle.lower() in zname.lower() for needle in name_contains)

    # Some guild report metadata may not include zone reliably.
    # Keep it for fight-level inspection rather than incorrectly dropping it.
    return True


def count_mythic_pulls(report_data: JsonDict, mythic_difficulty: int, midnight_zone_ids: set[int]) -> int:
    pulls = 0

    for fight in report_data.get("fights", []):
        encounter_id = fight.get("encounterID")
        difficulty = fight.get("difficulty")
        if not encounter_id or difficulty != mythic_difficulty:
            continue

        # v1 fight data may not include actual in-game zone. If it does, respect it.
        fight_zone = fight.get("gameZone", {}) or fight.get("zone", {})
        fight_zone_id = None
        if isinstance(fight_zone, dict):
            fight_zone_id = fight_zone.get("id")
        elif isinstance(fight_zone, int):
            fight_zone_id = fight_zone

        if fight_zone_id is not None and midnight_zone_ids and int(fight_zone_id) not in midnight_zone_ids:
            continue

        pulls += 1

    return pulls


def choose_one_report_per_day(
    report_candidates: list[tuple[JsonDict, JsonDict, int, str]],
    tz_name: str,
    zone_lookup: dict[int, str],
) -> list[ChosenReport]:
    """
    Input tuples:
        (report_meta, report_fights_data, mythic_pull_count, local_date)

    Selects the report with the highest mythic pull count for each day.
    """

    best_by_day: dict[str, tuple[JsonDict, JsonDict, int]] = {}

    for report_meta, report_data, pull_count, local_date in report_candidates:
        current = best_by_day.get(local_date)
        if current is None or pull_count > current[2]:
            best_by_day[local_date] = (report_meta, report_data, pull_count)

    chosen: list[ChosenReport] = []
    for local_date, (meta, data, pull_count) in sorted(best_by_day.items()):
        code = report_code(meta) or data.get("code", "")
        zid = report_zone_id(meta)
        zname = report_zone_name(meta, zone_lookup)

        chosen.append(
            ChosenReport(
                date=local_date,
                code=code,
                title=meta.get("title", data.get("title", code)),
                start_ms=int(meta.get("start", data.get("startTime", 0))),
                end_ms=int(meta.get("end", data.get("endTime", 0))),
                mythic_pull_count=pull_count,
                zone_id=zid,
                zone_name=zname,
            )
        )

    return chosen



def intervals_overlap(a_start: int, a_end: int, b_start: int, b_end: int, tolerance_ms: int) -> bool:
    latest_start = max(a_start, b_start)
    earliest_end = min(a_end, b_end)
    overlap = earliest_end - latest_start
    return overlap > tolerance_ms


def choose_reports_by_day_or_session(
    report_candidates: list[tuple[JsonDict, JsonDict, int, str]],
    tz_name: str,
    zone_lookup: dict[int, str],
    selection_config: JsonDict,
) -> list[ChosenReport]:
    """
    Selects reports for each day.

    v1.4 behaviour:
    - If combine_same_day_reports is false, keep old one-report-per-day logic.
    - If true, include multiple same-day reports if they look like split logs from
      the same raid night.
    - If reports overlap significantly, keep the one with more Mythic pulls.
    """

    if not selection_config.get("combine_same_day_reports", False):
        return choose_one_report_per_day(report_candidates, tz_name, zone_lookup)

    by_day: dict[str, list[tuple[JsonDict, JsonDict, int]]] = {}
    for meta, report_data, pulls, local_date in report_candidates:
        by_day.setdefault(local_date, []).append((meta, report_data, pulls))

    chosen: list[ChosenReport] = []
    max_gap_ms = int(selection_config.get("max_same_night_gap_minutes", 90)) * 60 * 1000
    max_span_ms = int(selection_config.get("max_raid_night_span_hours", 8)) * 60 * 60 * 1000
    overlap_tolerance_ms = int(selection_config.get("overlap_tolerance_minutes", 10)) * 60 * 1000

    for local_date, items in sorted(by_day.items()):
        items = sorted(items, key=lambda x: int(x[0].get("start", x[1].get("startTime", 0))))

        selected: list[tuple[JsonDict, JsonDict, int]] = []

        for candidate in items:
            meta, data, pulls = candidate
            c_start = int(meta.get("start", data.get("startTime", 0)))
            c_end = int(meta.get("end", data.get("endTime", 0)))

            if not selected:
                selected.append(candidate)
                continue

            # If it overlaps an already selected report, treat as duplicate and keep the higher-pull one.
            replaced = False
            duplicate = False
            for idx, existing in enumerate(selected):
                e_meta, e_data, e_pulls = existing
                e_start = int(e_meta.get("start", e_data.get("startTime", 0)))
                e_end = int(e_meta.get("end", e_data.get("endTime", 0)))

                if intervals_overlap(c_start, c_end, e_start, e_end, overlap_tolerance_ms):
                    duplicate = True
                    if pulls > e_pulls:
                        selected[idx] = candidate
                        replaced = True
                    break

            if duplicate:
                continue

            current_start = min(int(x[0].get("start", x[1].get("startTime", 0))) for x in selected)
            current_end = max(int(x[0].get("end", x[1].get("endTime", 0))) for x in selected)

            gap_to_window = min(abs(c_start - current_end), abs(current_start - c_end))
            proposed_start = min(current_start, c_start)
            proposed_end = max(current_end, c_end)
            proposed_span = proposed_end - proposed_start

            if gap_to_window <= max_gap_ms and proposed_span <= max_span_ms:
                selected.append(candidate)
            else:
                # Different session on same date. Keep only if it has more pulls than the current selected set.
                current_pulls = sum(x[2] for x in selected)
                if pulls > current_pulls:
                    selected = [candidate]

        for meta, data, pulls in selected:
            code = report_code(meta) or data.get("code", "")
            zid = report_zone_id(meta)
            zname = report_zone_name(meta, zone_lookup)

            chosen.append(
                ChosenReport(
                    date=local_date,
                    code=code,
                    title=meta.get("title", data.get("title", code)),
                    start_ms=int(meta.get("start", data.get("startTime", 0))),
                    end_ms=int(meta.get("end", data.get("endTime", 0))),
                    mythic_pull_count=pulls,
                    zone_id=zid,
                    zone_name=zname,
                )
            )

    return sorted(chosen, key=lambda x: (x.date, x.start_ms))
