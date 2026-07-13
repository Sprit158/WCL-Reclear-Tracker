from __future__ import annotations

from dataclasses import dataclass, asdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo
import csv
import json
import math
import re
import time

from guild_discovery import discover_guilds, select_guilds_around_own, write_discovered_guilds
from guild_fetcher import season_range_ms
from settings_manager import get_global_cache_dir, get_guild_profile_from_settings
from v2_report_tools import slugify_realm, v2_reports_to_v1_meta
from v2_setup import build_v2_client, save_client_token
from wowprogress_backup import find_wowprogress_backup_match, rows_from_wowprogress_backup_for_schedule_scan
from schedule_database import (
    connect_schedule_db,
    get_cached_endboss_kill,
    get_cached_report_fight_summary,
    get_latest_cached_reports,
    get_cached_reports,
    get_schedule_result_statuses,
    replace_raid_nights,
    should_skip_existing_schedule_result,
    upsert_discovered_guilds,
    upsert_endboss_kill_cache,
    upsert_report_fight_summary,
    upsert_report_cache,
    upsert_schedule_result,
)


JsonDict = dict[str, Any]


@dataclass(slots=True)
class NightSummary:
    date: str
    weekday: str
    week_start: str
    start_ms: int
    end_ms: int
    hours: float
    report_count: int
    report_codes: str
    zone_names: str


@dataclass(slots=True)
class ScheduleResult:
    guild: str
    realm: str
    region: str
    rank: int | None
    reports_found: int
    reports_used_for_schedule: int
    progression_cutoff_date: str
    progression_cutoff_source: str
    reports_after_cutoff_excluded: int
    raid_nights_found: int
    active_weeks: int
    inferred_days_per_week: float | None
    average_raid_days_per_active_week: float | None
    logged_window_hours_per_week: float | None
    inferred_hours_per_week: float | None
    inferred_raid_days: str
    is_likely_two_day: bool
    candidate_needs_deep_time_review: bool
    schedule_source: str
    schedule_confidence: str
    reason: str
    example_nights: str
    notes: str
    first_month_average_raid_days: float | None = None


@dataclass(slots=True)
class ScheduleFetchJobResult:
    row: dict
    reports: list[JsonDict]
    source: str
    fetched_from_api: bool
    error: str | None
    debug_lines: list[str]


@dataclass(slots=True)
class CoreScheduleAnalysis:
    core_days: list[str]
    coverage_by_day: dict[str, float]
    overtime_nights: int
    estimated_average: float
    ambiguous: bool
    confidence: str
    explanation: str


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_") or "unknown"


def report_start(report: JsonDict) -> int:
    return int(report.get("start", report.get("startTime", 0)) or 0)


def report_end(report: JsonDict) -> int:
    return int(report.get("end", report.get("endTime", 0)) or 0)


def report_code(report: JsonDict) -> str:
    return str(report.get("code") or report.get("id") or "")


def report_zone_name(report: JsonDict) -> str:
    zone = report.get("zone")
    if isinstance(zone, dict):
        return str(zone.get("name") or "")
    return str(report.get("zoneName") or "")


def report_zone_id(report: JsonDict) -> int | None:
    zone = report.get("zone")
    value = zone.get("id") if isinstance(zone, dict) else report.get("zoneID", zone)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def local_dt(ms: int, tz_name: str) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=ZoneInfo(tz_name))


def local_date(ms: int, tz_name: str) -> str:
    return local_dt(ms, tz_name).date().isoformat()


def week_start_date(date_text: str) -> str:
    d = datetime.strptime(date_text, "%Y-%m-%d").date()
    monday = d - timedelta(days=d.weekday())
    return monday.isoformat()


def weekday_name(date_text: str) -> str:
    d = datetime.strptime(date_text, "%Y-%m-%d").date()
    return ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][d.weekday()]


def cache_path_for_guild(config: JsonDict, guild: str, realm: str, region: str, start_ms: int, end_ms: int) -> Path:
    scan_cfg = config.get("comparison", {}).get("schedule_scan", {})
    folder = get_global_cache_dir(scan_cfg.get("cache_folder", "schedule_report_lists"))
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{slugify(region)}_{slugify(realm)}_{slugify(guild)}_{start_ms}_{end_ms}.json"


def fetch_guild_reports_v2_cached(
    client,
    conn,
    config: JsonDict,
    guild: str,
    realm: str,
    region: str,
    debug_lines: list[str],
) -> tuple[list[JsonDict], str]:
    season_config = config.get("season", {})
    scan_cfg = config.get("comparison", {}).get("schedule_scan", {})
    start_ms, end_ms = season_range_ms(season_config)
    force_refresh = bool(scan_cfg.get("force_refresh", False))
    legacy_json_fallback = bool(scan_cfg.get("legacy_json_cache_fallback", True))

    if not force_refresh:
        reports = get_cached_reports(conn, guild, realm, region, start_ms, end_ms)
        if reports is not None:
            debug_lines.append(f"{guild}-{realm}-{region} :: sqlite reports cache hit :: {len(reports)} reports")
            return reports, "sqlite_cache"
        reports = get_latest_cached_reports(
            conn, guild, realm, region, start_ms,
            float(scan_cfg.get("report_list_cache_ttl_hours", 168)),
        )
        if reports is not None:
            debug_lines.append(f"{guild}-{realm}-{region} :: fresh latest sqlite reports cache hit :: {len(reports)} reports")
            return reports, "sqlite_cache_latest"

    # Legacy JSON cache migration/fallback from v1.6.1.
    path = cache_path_for_guild(config, guild, realm, region, start_ms, end_ms)
    if legacy_json_fallback and path.exists() and not force_refresh:
        try:
            wrapper = json.loads(path.read_text(encoding="utf-8"))
            reports = wrapper.get("data", [])
            upsert_report_cache(conn, guild, realm, region, start_ms, end_ms, reports, "legacy_json_migrated")
            debug_lines.append(f"{guild}-{realm}-{region} :: legacy json cache migrated to sqlite :: {len(reports)} reports")
            return reports, "legacy_json_migrated"
        except Exception as e:
            debug_lines.append(f"{guild}-{realm}-{region} :: legacy json cache read failed :: {type(e).__name__}: {e}")

    reports_raw = client.fetch_guild_reports(
        guild_name=guild,
        guild_server_slug=slugify_realm(realm),
        guild_server_region=region.upper(),
        start_time=start_ms,
        end_time=end_ms,
        max_pages=20,
    )
    reports = v2_reports_to_v1_meta(reports_raw)

    upsert_report_cache(conn, guild, realm, region, start_ms, end_ms, reports, "wcl_v2_api")
    debug_lines.append(f"{guild}-{realm}-{region} :: reports api fetch stored in sqlite :: {len(reports)} reports")
    return reports, "wcl_v2_api"



def load_cached_or_legacy_reports_main_thread(
    conn,
    config: JsonDict,
    guild: str,
    realm: str,
    region: str,
    debug_lines: list[str],
) -> tuple[list[JsonDict], str] | None:
    """
    Main-thread cache lookup. Does not call WCL.
    """
    season_config = config.get("season", {})
    scan_cfg = config.get("comparison", {}).get("schedule_scan", {})
    start_ms, end_ms = season_range_ms(season_config)
    force_refresh = bool(scan_cfg.get("force_refresh", False))
    legacy_json_fallback = bool(scan_cfg.get("legacy_json_cache_fallback", True))

    if not force_refresh:
        reports = get_cached_reports(conn, guild, realm, region, start_ms, end_ms)
        if reports is not None:
            debug_lines.append(f"{guild}-{realm}-{region} :: sqlite reports cache hit :: {len(reports)} reports")
            return reports, "sqlite_cache"
        reports = get_latest_cached_reports(
            conn, guild, realm, region, start_ms,
            float(scan_cfg.get("report_list_cache_ttl_hours", 168)),
        )
        if reports is not None:
            debug_lines.append(f"{guild}-{realm}-{region} :: fresh latest sqlite reports cache hit :: {len(reports)} reports")
            return reports, "sqlite_cache_latest"

    path = cache_path_for_guild(config, guild, realm, region, start_ms, end_ms)
    if legacy_json_fallback and path.exists() and not force_refresh:
        try:
            wrapper = json.loads(path.read_text(encoding="utf-8"))
            reports = wrapper.get("data", [])
            upsert_report_cache(conn, guild, realm, region, start_ms, end_ms, reports, "legacy_json_migrated")
            debug_lines.append(f"{guild}-{realm}-{region} :: legacy json cache migrated to sqlite :: {len(reports)} reports")
            return reports, "legacy_json_migrated"
        except Exception as e:
            debug_lines.append(f"{guild}-{realm}-{region} :: legacy json cache read failed :: {type(e).__name__}: {e}")

    return None


def fetch_guild_reports_api_worker(row: dict, config: JsonDict) -> ScheduleFetchJobResult:
    """
    Worker-thread API fetch only. No SQLite writes in this function.
    """
    guild = row["guild"]
    realm = row["realm"]
    region = row["region"]
    scan_cfg = config.get("comparison", {}).get("schedule_scan", {})
    season_config = config.get("season", {})
    start_ms, end_ms = season_range_ms(season_config)
    max_retries = int(scan_cfg.get("max_retry_attempts", 2))
    delay = float(scan_cfg.get("wcl_request_delay_seconds", 0.1))
    debug: list[str] = []

    for attempt in range(1, max_retries + 1):
        try:
            if delay and attempt > 1:
                time.sleep(delay * attempt)

            client = build_v2_client(config)
            reports_raw = client.fetch_guild_reports(
                guild_name=guild,
                guild_server_slug=slugify_realm(realm),
                guild_server_region=region.upper(),
                start_time=start_ms,
                end_time=end_ms,
                max_pages=20,
            )
            reports = v2_reports_to_v1_meta(reports_raw)
            debug.append(f"{guild}-{realm}-{region} :: reports api fetch worker success :: {len(reports)} reports")
            return ScheduleFetchJobResult(
                row=row,
                reports=reports,
                source="wcl_v2_api",
                fetched_from_api=True,
                error=None,
                debug_lines=debug,
            )

        except Exception as e:
            debug.append(f"{guild}-{realm}-{region} :: worker attempt {attempt}/{max_retries} failed :: {type(e).__name__}: {e}")
            if attempt >= max_retries:
                return ScheduleFetchJobResult(
                    row=row,
                    reports=[],
                    source="wcl_v2_api",
                    fetched_from_api=True,
                    error=f"{type(e).__name__}: {e}",
                    debug_lines=debug,
                )

    return ScheduleFetchJobResult(
        row=row,
        reports=[],
        source="wcl_v2_api",
        fetched_from_api=True,
        error="Unknown worker failure",
        debug_lines=debug,
    )


def classify_and_store_schedule_result(
    conn,
    config: JsonDict,
    row: dict,
    reports: list[JsonDict],
    source: str,
    tz_name: str,
    debug_lines: list[str],
) -> ScheduleResult:
    guild = row["guild"]
    realm = row["realm"]
    region = row["region"]

    if not reports:
        backup_result = backup_or_zero_reports_schedule_result(config, row, debug_lines)
        if backup_result is not None:
            upsert_schedule_result(conn, backup_result)
            replace_raid_nights(conn, guild, realm, region, [])
            return backup_result

    target_reports, unknown_reports, other_reports = filter_target_raid_reports(reports, config)
    debug_lines.append(
        f"{guild}-{realm}-{region} :: report zone filter target={len(target_reports)} "
        f"unknown_excluded={len(unknown_reports)} other_excluded={len(other_reports)}"
    )
    reports_for_schedule, excluded_after_cutoff, cutoff_date, cutoff_source = filter_reports_to_progression_cutoff(
        conn, target_reports, row, config, debug_lines
    )
    hydrate_short_report_mythic_evidence(conn, reports_for_schedule, config, debug_lines)
    nights = build_nights_from_reports(reports_for_schedule, config, tz_name)
    result = classify_schedule(
        guild=guild,
        realm=realm,
        region=region,
        rank=row.get("rank"),
        reports=reports_for_schedule,
        nights=nights,
        config=config,
        source=source,
        progression_cutoff_date=cutoff_date,
        progression_cutoff_source=cutoff_source,
        reports_after_cutoff_excluded=excluded_after_cutoff,
    )

    debug_lines.append(
        f"{guild}-{realm}-{region} :: nights={len(nights)} active_weeks={result.active_weeks} "
        f"avg_days={result.average_raid_days_per_active_week} median_nights={result.inferred_days_per_week} "
        f"logged_window_hours={result.logged_window_hours_per_week} candidate={result.is_likely_two_day}"
    )

    upsert_schedule_result(conn, result)
    replace_raid_nights(conn, guild, realm, region, nights)

    for night in nights[:12]:
        debug_lines.append(
            f"  night {night.date} {night.weekday} logged_window_hours={night.hours} reports={night.report_count} "
            f"codes={night.report_codes} zones={night.zone_names}"
        )

    return result




def declared_only_backup_schedule_result(row: dict, debug_lines: list[str]) -> ScheduleResult:
    guild = row["guild"]
    realm = row["realm"]
    region = row["region"]
    declared = str(row.get("wowprogress_declared_raids_week") or "1-2")
    progress = str(row.get("wowprogress_progress") or "")
    notes = str(row.get("wowprogress_notes") or "")

    debug_lines.append(
        f"{guild}-{realm}-{region} :: declared-only backup source :: "
        f"rank={row.get('rank')} declared_raids_week={declared}"
    )

    note_parts = [
        f"declared_raids_week={declared}",
        "source=WoWProgress screenshot backup",
        "WCL not queried in declared-only scan",
    ]
    if progress:
        note_parts.append(f"progress={progress}")
    if notes:
        note_parts.append(f"backup_notes={notes}")

    return ScheduleResult(
        guild=guild,
        realm=realm,
        region=region,
        rank=row.get("rank"),
        reports_found=0,
        reports_used_for_schedule=0,
        progression_cutoff_date=str(row.get("endboss_kill_date") or ""),
        progression_cutoff_source=str(row.get("endboss_kill_source") or ""),
        reports_after_cutoff_excluded=0,
        raid_nights_found=0,
        active_weeks=0,
        inferred_days_per_week=None,
        average_raid_days_per_active_week=None,
        logged_window_hours_per_week=None,
        inferred_hours_per_week=None,
        inferred_raid_days=f"declared {declared} raids/week",
        is_likely_two_day=True,
        candidate_needs_deep_time_review=False,
        schedule_source="wowprogress_screenshot_backup_declared_only",
        schedule_confidence="declared_only",
        reason=f"Selected from WoWProgress backup as declared {declared} raids/week. WCL not queried.",
        example_nights="",
        notes="; ".join(note_parts),
    )


def wowprogress_backup_schedule_result(row: dict, match, trigger_reason: str, debug_lines: list[str]) -> ScheduleResult:
    guild = row["guild"]
    realm = row["realm"]
    region = row["region"]
    debug_lines.append(
        f"{guild}-{realm}-{region} :: wowprogress backup match :: "
        f"rank={match.rank} declared_raids_week={match.declared_raids_week} quality={match.match_quality} trigger={trigger_reason}"
    )
    notes_parts = [
        f"WoWProgress backup rank={match.rank}",
        f"declared_raids_week={match.declared_raids_week}",
        f"match_quality={match.match_quality}",
    ]
    if match.recruiting_flag:
        notes_parts.append(f"recruiting_flag={match.recruiting_flag}")
    if match.notes:
        notes_parts.append(f"backup_notes={match.notes}")

    return ScheduleResult(
        guild=guild,
        realm=realm,
        region=region,
        rank=row.get("rank") or match.rank,
        reports_found=0,
        reports_used_for_schedule=0,
        progression_cutoff_date=str(row.get("endboss_kill_date") or ""),
        progression_cutoff_source=str(row.get("endboss_kill_source") or ""),
        reports_after_cutoff_excluded=0,
        raid_nights_found=0,
        active_weeks=0,
        inferred_days_per_week=None,
        average_raid_days_per_active_week=None,
        logged_window_hours_per_week=None,
        inferred_hours_per_week=None,
        inferred_raid_days=f"declared {match.declared_raids_week} raids/week",
        is_likely_two_day=True,
        candidate_needs_deep_time_review=False,
        schedule_source="wowprogress_screenshot_backup_declared_only",
        schedule_confidence="declared_only",
        reason=f"{trigger_reason}; matched WoWProgress screenshot backup as declared {match.declared_raids_week} raids/week. Not measured hours.",
        example_nights="",
        notes="; ".join(notes_parts),
    )


def backup_or_error_schedule_result(config: JsonDict, row: dict, error_text: str, debug_lines: list[str]) -> ScheduleResult:
    backup_cfg = config.get("comparison", {}).get("wowprogress_backup", {})
    use_on_error = bool(backup_cfg.get("use_when_wcl_lookup_failed", True))
    if use_on_error:
        match = find_wowprogress_backup_match(
            config,
            guild=row["guild"],
            realm=row["realm"],
            region=row["region"],
        )
        if match is not None:
            result = wowprogress_backup_schedule_result(row, match, f"WCL lookup/API failed: {error_text}", debug_lines)
            if config.get("comparison", {}).get("schedule_scan", {}).get("actual_schedule_verification_enabled", False):
                result.is_likely_two_day = False
                result.candidate_needs_deep_time_review = False
                result.schedule_source = "wowprogress_backup_unverified_wcl_error"
                result.schedule_confidence = "unverified"
                result.reason = f"Could not verify actual raid days from WCL: {error_text}"
            return result

    return error_schedule_result(row, error_text)


def backup_or_zero_reports_schedule_result(config: JsonDict, row: dict, debug_lines: list[str]) -> ScheduleResult | None:
    backup_cfg = config.get("comparison", {}).get("wowprogress_backup", {})
    use_on_zero = bool(backup_cfg.get("use_when_wcl_reports_zero", True))
    if not use_on_zero:
        return None

    match = find_wowprogress_backup_match(
        config,
        guild=row["guild"],
        realm=row["realm"],
        region=row["region"],
    )
    if match is None:
        return None

    result = wowprogress_backup_schedule_result(row, match, "WCL returned 0 public reports", debug_lines)
    if config.get("comparison", {}).get("schedule_scan", {}).get("actual_schedule_verification_enabled", False):
        result.is_likely_two_day = False
        result.candidate_needs_deep_time_review = False
        result.schedule_source = "wowprogress_backup_unverified_no_public_reports"
        result.schedule_confidence = "unverified"
        result.reason = "No public WCL reports were available, so actual raid days could not be verified."
    return result

def error_schedule_result(row: dict, error_text: str) -> ScheduleResult:
    return ScheduleResult(
        guild=row["guild"],
        realm=row["realm"],
        region=row["region"],
        rank=row.get("rank"),
        reports_found=0,
        reports_used_for_schedule=0,
        progression_cutoff_date=str(row.get("endboss_kill_date") or ""),
        progression_cutoff_source=str(row.get("endboss_kill_source") or ""),
        reports_after_cutoff_excluded=0,
        raid_nights_found=0,
        active_weeks=0,
        inferred_days_per_week=None,
        average_raid_days_per_active_week=None,
        logged_window_hours_per_week=None,
        inferred_hours_per_week=None,
        inferred_raid_days="",
        is_likely_two_day=False,
        candidate_needs_deep_time_review=False,
        schedule_source="wcl_report_list_candidate_filter",
        schedule_confidence="error",
        reason=error_text,
        example_nights="",
        notes="",
    )

def report_is_evening_candidate(report: JsonDict, config: JsonDict, tz_name: str) -> bool:
    scan_cfg = config.get("comparison", {}).get("schedule_scan", {})
    start = report_start(report)
    end = report_end(report)
    if not start or not end or end <= start:
        return False

    dt = local_dt(start, tz_name)
    min_hour = int(scan_cfg.get("raid_start_hour_min", 15))
    max_hour = int(scan_cfg.get("raid_start_hour_max", 23))

    if dt.hour < min_hour or dt.hour > max_hour:
        return False

    # Keep zone filtering deliberately loose. Some real raid reports can have bad report-level zone metadata.
    return True


def report_contains_mythic_boss_fight(report: JsonDict) -> bool:
    if bool(report.get("contains_mythic_boss_fight", False)):
        return True
    return any(
        int(fight.get("difficulty") or 0) == 5 and bool(fight.get("encounterID"))
        for fight in report.get("fights", [])
        if isinstance(fight, dict)
    )


def hydrate_short_report_mythic_evidence(
    conn,
    reports: list[JsonDict],
    config: JsonDict,
    debug_lines: list[str],
) -> None:
    """Inspect only reports that duration filtering would otherwise discard.

    Fight summaries are immutable after a WCL report is complete, so cache them
    permanently by report code. This makes subsequent scans use zero WCL points
    for the same short reports while preserving genuine short Mythic raid nights.
    """
    scan_cfg = config.get("comparison", {}).get("schedule_scan", {})
    minimum_ms = int(scan_cfg.get("minimum_counted_raid_day_minutes", 15)) * 60 * 1000
    short_reports = [
        report for report in reports
        if report_code(report)
        and report_start(report)
        and report_end(report) > report_start(report)
        and report_end(report) - report_start(report) < minimum_ms
    ]
    if not short_reports:
        return

    client = None
    fetched = 0
    cache_hits = 0
    for report in short_reports:
        code = report_code(report)
        cached = get_cached_report_fight_summary(conn, code)
        if cached is not None:
            report["fights"] = cached["fights"]
            report["contains_mythic_boss_fight"] = cached["contains_mythic_boss_fight"]
            cache_hits += 1
            continue

        try:
            if client is None:
                client = build_v2_client(config)
            data = client.fetch_report_fights(code)
            fights = data.get("fights", [])
            contains_mythic = any(
                int(fight.get("difficulty") or 0) == 5 and bool(fight.get("encounterID"))
                for fight in fights
                if isinstance(fight, dict)
            )
            report["fights"] = fights
            report["contains_mythic_boss_fight"] = contains_mythic
            upsert_report_fight_summary(conn, code, fights, contains_mythic)
            fetched += 1
        except Exception as e:
            debug_lines.append(
                f"short report fight check failed report={code} :: {type(e).__name__}: {e}"
            )

    if client is not None:
        save_client_token(client)
    debug_lines.append(
        f"short report mythic checks :: reports={len(short_reports)} cache_hits={cache_hits} "
        f"wcl_fetches={fetched}"
    )


def report_date_utc_from_ms(ms: int | None) -> str:
    if not ms:
        return ""
    from datetime import timezone
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date().isoformat()


def absolute_fight_time_ms(report_start_ms: int, fight_time: int) -> int:
    # WCL fight times are normally relative to report start. If already epoch ms, keep it.
    return fight_time if fight_time > 1_000_000_000_000 else report_start_ms + fight_time


def is_endboss_kill_fight(fight: JsonDict, config: JsonDict) -> bool:
    scan_cfg = config.get("comparison", {}).get("schedule_scan", {})
    names = [str(x).lower() for x in scan_cfg.get("endboss_encounter_names", ["Midnight Falls", "Lura"])]
    name = str(fight.get("name") or "").lower()
    if not bool(fight.get("kill", False)):
        return False
    if int(fight.get("difficulty") or 0) != 5:
        return False
    return any(n and n in name for n in names)


def find_endboss_kill_from_wcl_reports(
    conn,
    config: JsonDict,
    row: dict,
    reports: list[JsonDict],
    debug_lines: list[str],
) -> tuple[int | None, str, str]:
    scan_cfg = config.get("comparison", {}).get("schedule_scan", {})
    if not scan_cfg.get("fallback_find_endboss_kill_from_wcl", True):
        return None, "", ""

    guild = row["guild"]
    realm = row["realm"]
    region = row["region"]
    ttl = float(scan_cfg.get("endboss_kill_cache_ttl_hours", 720))

    cached = get_cached_endboss_kill(conn, guild, realm, region, ttl)
    if cached is not None:
        if cached.get("found"):
            debug_lines.append(f"{guild}-{realm}-{region} :: endboss kill cache hit :: {cached.get('kill_date')}")
            return int(cached["kill_timestamp_ms"]), cached.get("kill_date", ""), cached.get("source", "wcl_endboss_kill_cache")
        debug_lines.append(f"{guild}-{realm}-{region} :: endboss kill cache hit :: not found")
        return None, "", ""

    client = build_v2_client(config)
    checked = 0
    sorted_reports = sorted(
        [r for r in reports if report_start(r) and report_code(r)],
        key=lambda r: report_start(r),
    )

    for report in sorted_reports:
        code = report_code(report)
        try:
            data = client.fetch_report_fights(code)
            checked += 1
        except Exception as e:
            debug_lines.append(f"{guild}-{realm}-{region} :: endboss kill lookup failed report={code} :: {type(e).__name__}: {e}")
            continue

        report_start_ms = int(data.get("startTime") or report_start(report) or 0)
        fights = data.get("fights", [])
        upsert_report_fight_summary(
            conn,
            code,
            fights,
            any(
                int(fight.get("difficulty") or 0) == 5 and bool(fight.get("encounterID"))
                for fight in fights
                if isinstance(fight, dict)
            ),
        )
        for fight in fights:
            if is_endboss_kill_fight(fight, config):
                kill_ms = absolute_fight_time_ms(report_start_ms, int(fight.get("endTime") or 0))
                kill_date = report_date_utc_from_ms(kill_ms)
                source = f"wcl_fight_summary:{code}"
                upsert_endboss_kill_cache(conn, guild, realm, region, kill_ms, kill_date, source, checked, True)
                debug_lines.append(f"{guild}-{realm}-{region} :: endboss kill found from WCL :: {kill_date} report={code}")
                return kill_ms, kill_date, source

    upsert_endboss_kill_cache(conn, guild, realm, region, None, "", "wcl_fight_summary:not_found", checked, False)
    debug_lines.append(f"{guild}-{realm}-{region} :: endboss kill not found from WCL after {checked} reports")
    return None, "", ""


def progression_cutoff_ms_from_row(row: dict, config: JsonDict) -> tuple[int | None, str]:
    scan_cfg = config.get("comparison", {}).get("schedule_scan", {})
    if not scan_cfg.get("use_guild_endboss_kill_cutoff", True):
        return None, ""

    raw = row.get("endboss_kill_timestamp_ms")
    if raw in (None, ""):
        return None, ""

    try:
        kill_ms = int(raw)
    except Exception:
        return None, ""

    include_kill_day = bool(scan_cfg.get("include_kill_day_in_schedule", True))
    if include_kill_day:
        # Include reports up to the end of the UTC kill day. This avoids cutting out
        # earlier same-day progression logs if the kill happened late in the evening.
        from datetime import datetime, timezone, time as dt_time
        kill_date = datetime.fromtimestamp(kill_ms / 1000, tz=timezone.utc).date()
        end_of_day = datetime.combine(kill_date, dt_time.max, tzinfo=timezone.utc)
        cutoff_ms = int(end_of_day.timestamp() * 1000)
    else:
        cutoff_ms = kill_ms

    source = row.get("endboss_kill_source") or "discovery_endboss_kill"
    return cutoff_ms, str(source)


def filter_reports_to_progression_cutoff(
    conn,
    reports: list[JsonDict],
    row: dict,
    config: JsonDict,
    debug_lines: list[str],
) -> tuple[list[JsonDict], int, str, str]:
    cutoff_ms, source = progression_cutoff_ms_from_row(row, config)

    if not cutoff_ms:
        cutoff_ms, cutoff_date_hint, source = find_endboss_kill_from_wcl_reports(
            conn, config, row, reports, debug_lines
        )
    else:
        cutoff_date_hint = ""

    if not cutoff_ms:
        return reports, 0, "", ""

    filtered = [r for r in reports if report_start(r) and report_start(r) <= cutoff_ms]
    excluded = len(reports) - len(filtered)
    cutoff_date = cutoff_date_hint or report_date_utc_from_ms(cutoff_ms)
    debug_lines.append(
        f"{row['guild']}-{row['realm']}-{row['region']} :: progression cutoff {cutoff_date} "
        f"source={source} excluded_reports_after_cutoff={excluded}"
    )
    return filtered, excluded, cutoff_date, source


def build_nights_from_reports(reports: list[JsonDict], config: JsonDict, tz_name: str) -> list[NightSummary]:
    scan_cfg = config.get("comparison", {}).get("schedule_scan", {})
    # Short farm/kill logs still prove that the guild raided on that date.
    min_span_ms = int(scan_cfg.get("minimum_counted_raid_day_minutes", 15)) * 60 * 1000
    max_span_ms = int(float(scan_cfg.get("max_raid_night_span_hours", 8)) * 60 * 60 * 1000)

    by_date: dict[str, list[JsonDict]] = {}

    for report in reports:
        if not report_is_evening_candidate(report, config, tz_name):
            continue
        d = local_date(report_start(report), tz_name)
        by_date.setdefault(d, []).append(report)

    nights: list[NightSummary] = []

    for date_text, items in sorted(by_date.items()):
        starts = [report_start(r) for r in items if report_start(r)]
        ends = [report_end(r) for r in items if report_end(r)]
        if not starts or not ends:
            continue

        start_ms = min(starts)
        end_ms = max(ends)
        span_ms = end_ms - start_ms

        # Any report containing a Mythic boss fight proves this was a raid day,
        # even if the logger started late or the report lasted only a few minutes.
        has_mythic_fight = any(report_contains_mythic_boss_fight(r) for r in items)
        if span_ms < min_span_ms and not has_mythic_fight:
            continue

        if span_ms > max_span_ms:
            # Cap obviously over-long page/report spans instead of letting one bad report ruin weekly hours.
            end_ms = start_ms + max_span_ms
            span_ms = max_span_ms

        codes = ", ".join(sorted({report_code(r) for r in items if report_code(r)}))
        zones = ", ".join(sorted({report_zone_name(r) for r in items if report_zone_name(r)}))

        nights.append(
            NightSummary(
                date=date_text,
                weekday=weekday_name(date_text),
                week_start=week_start_date(date_text),
                start_ms=start_ms,
                end_ms=end_ms,
                hours=round(span_ms / 3600000, 2),
                report_count=len(items),
                report_codes=codes,
                zone_names=zones,
            )
        )

    return nights


def infer_core_raid_days(
    nights: list[NightSummary],
    active_weeks: int,
    median_nights_per_week: float,
    config: JsonDict,
) -> CoreScheduleAnalysis:
    """Infer a stable weekly schedule without treating occasional overtime as a core day.

    Weekday support is based on distinct reset weeks, not report count. The second
    core day uses an adaptive threshold relative to the guild's strongest day.
    Third and later days require stronger recurrence *and* a matching weekly
    median. Close, competing weekdays are reported as ambiguous (often a schedule
    change or rotating raid night) instead of forcing a confident answer.
    """
    ordered_days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    day_order = {day: index for index, day in enumerate(ordered_days)}
    scan_cfg = config.get("comparison", {}).get("schedule_scan", {})

    day_weeks: dict[str, set[str]] = {day: set() for day in ordered_days}
    for night in nights:
        day_weeks.setdefault(night.weekday, set()).add(night.week_start)

    coverage = {
        day: (len(day_weeks.get(day, set())) / active_weeks if active_weeks else 0.0)
        for day in ordered_days
    }
    ranked_days = sorted(
        ordered_days,
        key=lambda day: (-coverage[day], day_order[day]),
    )

    minimum_coverage = float(scan_cfg.get("core_day_min_coverage", 0.35))
    second_min_coverage = float(scan_cfg.get("second_core_day_min_coverage", 0.45))
    second_relative = float(scan_cfg.get("second_core_day_relative_to_strongest", 0.60))
    extra_min_coverage = float(scan_cfg.get("extra_core_day_min_coverage", 0.60))
    extra_relative = float(scan_cfg.get("extra_core_day_relative_to_strongest", 0.75))
    ambiguity_gap = float(scan_cfg.get("core_day_ambiguity_gap", 0.12))
    minimum_occurrences = max(2, math.ceil(active_weeks * minimum_coverage))

    strongest = coverage[ranked_days[0]] if ranked_days else 0.0
    core_days_ranked: list[str] = []
    if ranked_days and len(day_weeks[ranked_days[0]]) >= minimum_occurrences:
        core_days_ranked.append(ranked_days[0])

    if len(ranked_days) > 1 and core_days_ranked and median_nights_per_week >= 1.5:
        second = ranked_days[1]
        second_threshold = max(second_min_coverage, strongest * second_relative)
        if len(day_weeks[second]) >= minimum_occurrences and coverage[second] >= second_threshold:
            core_days_ranked.append(second)

    # Three- and four-day guilds must look like three-/four-day guilds in a
    # typical week. This prevents a late progression push from becoming a core day.
    for day in ranked_days[2:]:
        required_weekly_median = len(core_days_ranked) + 0.5
        extra_threshold = max(extra_min_coverage, strongest * extra_relative)
        if (
            core_days_ranked
            and median_nights_per_week >= required_weekly_median
            and len(day_weeks[day]) >= minimum_occurrences
            and coverage[day] >= extra_threshold
        ):
            core_days_ranked.append(day)
        else:
            break

    ambiguous = False
    competing_day = ""
    if len(core_days_ranked) >= 2 and len(ranked_days) > len(core_days_ranked):
        last_core = core_days_ranked[-1]
        next_day = ranked_days[len(core_days_ranked)]
        if (
            coverage[next_day] >= minimum_coverage
            and coverage[last_core] - coverage[next_day] <= ambiguity_gap
        ):
            ambiguous = True
            competing_day = next_day

    core_day_set = set(core_days_ranked)
    overtime_nights = sum(1 for night in nights if night.weekday not in core_day_set)
    observed_average = round(len(nights) / active_weeks, 2) if active_weeks else 0.0
    estimated_average = (
        round(len(core_days_ranked) + overtime_nights / active_weeks, 2)
        if core_days_ranked and active_weeks
        else observed_average
    )
    # If several weekdays are tied, we cannot safely label the others overtime.
    # Keep the honest observed value until the pattern becomes stable.
    if ambiguous:
        estimated_average = observed_average

    min_active_weeks = int(scan_cfg.get("minimum_active_weeks", 4))
    core_coverages = [coverage[day] for day in core_days_ranked]
    if active_weeks < min_active_weeks or ambiguous or not core_days_ranked:
        confidence = "low"
    elif active_weeks >= 8 and core_coverages and min(core_coverages) >= 0.70:
        confidence = "high"
    else:
        confidence = "medium"

    coverage_text = ", ".join(
        f"{day} {len(day_weeks[day])}/{active_weeks}"
        for day in ranked_days
        if day_weeks[day]
    )
    if ambiguous:
        explanation = (
            f"Weekday pattern is ambiguous: {competing_day} recurs almost as often as the "
            f"least-supported selected day. This may be a schedule change or rotating day. "
            f"Week support: {coverage_text}."
        )
    else:
        explanation = f"Week support: {coverage_text}."

    return CoreScheduleAnalysis(
        core_days=sorted(core_days_ranked, key=lambda day: day_order[day]),
        coverage_by_day=coverage,
        overtime_nights=overtime_nights,
        estimated_average=estimated_average,
        ambiguous=ambiguous,
        confidence=confidence,
        explanation=explanation,
    )


def classify_schedule(
    guild: str,
    realm: str,
    region: str,
    rank: int | None,
    reports: list[JsonDict],
    nights: list[NightSummary],
    config: JsonDict,
    source: str,
    progression_cutoff_date: str = "",
    progression_cutoff_source: str = "",
    reports_after_cutoff_excluded: int = 0,
) -> ScheduleResult:
    scan_cfg = config.get("comparison", {}).get("schedule_scan", {})

    by_week: dict[str, list[NightSummary]] = {}
    for night in nights:
        by_week.setdefault(night.week_start, []).append(night)

    active_weeks = len(by_week)

    if not nights or active_weeks == 0:
        return ScheduleResult(
            guild=guild,
            realm=realm,
            region=region,
            rank=rank,
            reports_found=len(reports),
            reports_used_for_schedule=0,
            progression_cutoff_date=progression_cutoff_date,
            progression_cutoff_source=progression_cutoff_source,
            reports_after_cutoff_excluded=reports_after_cutoff_excluded,
            raid_nights_found=0,
            active_weeks=0,
            inferred_days_per_week=None,
            average_raid_days_per_active_week=None,
            logged_window_hours_per_week=None,
            inferred_hours_per_week=None,
            inferred_raid_days="",
            is_likely_two_day=False,
            candidate_needs_deep_time_review=False,
            schedule_source="wcl_report_list_candidate_filter",
            schedule_confidence="none",
            reason="No report-list raid-night candidates passed the cheap first-pass filters.",
            example_nights="",
            notes=f"report_source={source}; progression_cutoff={progression_cutoff_date or 'none'}; excluded_after_cutoff={reports_after_cutoff_excluded}; report-list candidate scan; short-report Mythic fight evidence checked when needed",
        )

    weekly_nights = [len(items) for items in by_week.values()]
    weekly_hours = [sum(n.hours for n in items) for items in by_week.values()]

    med_nights = float(median(weekly_nights))
    observed_avg_nights = round(sum(weekly_nights) / len(weekly_nights), 2)
    med_hours = round(float(median(weekly_hours)), 2)

    # "First month" means the first four reset weeks containing the configured
    # season start date. Include zero-night weeks so this metric is not inflated
    # by looking only at weeks where the guild uploaded a report.
    season_start = datetime.strptime(
        config.get("season", {}).get("start_date", "2026-01-01"), "%Y-%m-%d"
    ).date()
    first_reset_week = season_start - timedelta(days=season_start.weekday())
    first_month_end = first_reset_week + timedelta(days=28)
    first_month_night_dates = {
        datetime.strptime(night.date, "%Y-%m-%d").date()
        for night in nights
        if first_reset_week <= datetime.strptime(night.date, "%Y-%m-%d").date() < first_month_end
    }
    first_month_avg_days = round(len(first_month_night_dates) / 4, 2)

    core_analysis = infer_core_raid_days(nights, active_weeks, med_nights, config)
    core_days = core_analysis.core_days
    inferred_days = ", ".join(core_days)
    overtime_nights = core_analysis.overtime_nights
    estimated_avg_nights = core_analysis.estimated_average

    min_active_weeks = int(scan_cfg.get("minimum_active_weeks", 4))
    min_nights = float(scan_cfg.get("candidate_two_day_min_nights_per_week", 1.5))
    min_hours = float(scan_cfg.get("candidate_peer_min_logged_hours_per_week", 4.5))

    # Classify the recurring schedule separately from overtime. A two-core-day
    # guild remains a two-day guild even when progression adds extra days.
    is_likely_two = (
        active_weeks >= min_active_weeks
        and len(core_days) == 2
        and not core_analysis.ambiguous
        and med_nights >= min_nights
        and med_hours >= min_hours
    )

    if active_weeks < min_active_weeks:
        confidence = "low"
        reason = f"Only {active_weeks} active weeks found; minimum is {min_active_weeks}."
    elif core_analysis.ambiguous:
        confidence = "low"
        reason = (
            f"{core_analysis.explanation} Estimated {estimated_avg_nights:g} days/week; "
            f"observed average {observed_avg_nights:g} and median {med_nights:g} nights/week."
        )
    elif is_likely_two:
        confidence = core_analysis.confidence
        reason = (
            f"Core days {inferred_days}; estimated {estimated_avg_nights:g} days/week including "
            f"{overtime_nights} overtime day(s) across {active_weeks} active weeks. "
            f"Observed average {observed_avg_nights:g}; median {med_nights:g} nights/week and "
            f"{med_hours:g} logged-window hours/week. {core_analysis.explanation}"
        )
    else:
        confidence = core_analysis.confidence
        reason = (
            f"Detected {len(core_days)} recurring core day(s) ({inferred_days or 'none'}); "
            f"estimated {estimated_avg_nights:g} days/week including overtime, observed average "
            f"{observed_avg_nights:g}, median {med_nights:g} nights/week and {med_hours:g} logged-window hours/week. "
            f"{core_analysis.explanation}"
        )

    examples = []
    for n in nights[:8]:
        start = local_dt(n.start_ms, scan_cfg.get("timezone", "Europe/London")).strftime("%H:%M")
        end = local_dt(n.end_ms, scan_cfg.get("timezone", "Europe/London")).strftime("%H:%M")
        examples.append(f"{n.date} {n.weekday} {start}-{end} logged_window={n.hours}h")

    return ScheduleResult(
        guild=guild,
        realm=realm,
        region=region,
        rank=rank,
        reports_found=len(reports),
        reports_used_for_schedule=sum(n.report_count for n in nights),
        progression_cutoff_date=progression_cutoff_date,
        progression_cutoff_source=progression_cutoff_source,
        reports_after_cutoff_excluded=reports_after_cutoff_excluded,
        raid_nights_found=len(nights),
        active_weeks=active_weeks,
        inferred_days_per_week=med_nights,
        average_raid_days_per_active_week=estimated_avg_nights,
        logged_window_hours_per_week=med_hours,
        inferred_hours_per_week=med_hours,
        inferred_raid_days=inferred_days,
        is_likely_two_day=is_likely_two,
        candidate_needs_deep_time_review=is_likely_two,
        schedule_source="wcl_report_list_candidate_filter",
        schedule_confidence=confidence,
        reason=reason,
        example_nights=" | ".join(examples),
        notes=f"report_source={source}; progression_cutoff={progression_cutoff_date or 'none'}; excluded_after_cutoff={reports_after_cutoff_excluded}; report-list candidate scan; fight summaries fetched and cached only for otherwise-too-short reports",
        first_month_average_raid_days=first_month_avg_days,
    )


def filter_target_raid_reports(
    reports: list[JsonDict],
    config: JsonDict,
) -> tuple[list[JsonDict], list[JsonDict], list[JsonDict]]:
    """Split report-list metadata into target raid, unknown-zone and other-zone rows."""

    zone_cfg = config.get("midnight_raid_zones", {})
    target_ids = {int(value) for value in zone_cfg.get("zone_ids", []) if value is not None}
    target_names = [str(value).lower() for value in zone_cfg.get("name_contains", []) if value]
    target: list[JsonDict] = []
    unknown: list[JsonDict] = []
    other: list[JsonDict] = []

    for report in reports:
        zone_id = report_zone_id(report)
        zone_name = report_zone_name(report).strip()
        if zone_id is None and not zone_name:
            unknown.append(report)
        elif (zone_id is not None and zone_id in target_ids) or any(
            name in zone_name.lower() for name in target_names
        ):
            target.append(report)
        else:
            other.append(report)

    return target, unknown, other


def points_used_between(before: JsonDict, after: JsonDict) -> float | None:
    try:
        start = float(before.get("pointsSpentThisHour"))
        end = float(after.get("pointsSpentThisHour"))
    except (TypeError, ValueError):
        return None
    if end < start:
        return None  # The hourly window reset during the test.
    return round(end - start, 2)


def run_single_guild_schedule_test(
    config: JsonDict,
    logger,
    guild: str | None = None,
    realm: str | None = None,
    region: str | None = None,
) -> None:
    """Measure the WCL point cost of schedule inference for exactly one guild."""

    from api.wcl_api_v2 import WCLV2ApiError
    from settings_manager import SettingsError

    profile = get_guild_profile_from_settings()
    guild = (guild or (profile.name if profile else "")).strip()
    realm = (realm or (profile.realm if profile else "")).strip()
    region = (region or (profile.region if profile else "EU")).strip().upper()
    if not guild or not realm:
        logger.print("No guild was supplied and no saved guild profile was found.")
        logger.print("Save a guild first, or provide --guild and --realm with this test.")
        return

    start_ms, end_ms = season_range_ms(config.get("season", {}))
    tz_name = config.get("season", {}).get("timezone", "Europe/London")
    logger.print(f"One-guild schedule and WCL point-cost test: {guild}-{realm}-{region}")
    logger.print("This fetches report-list metadata plus cached fight summaries only for otherwise-too-short reports.")

    try:
        client = build_v2_client(config)
        before_payload = client.test_query()
        before = before_payload.get("data", {}).get("rateLimitData", {})

        reports_raw = client.fetch_guild_reports(
            guild_name=guild,
            guild_server_slug=slugify_realm(realm),
            guild_server_region=region,
            start_time=start_ms,
            end_time=end_ms,
            limit=100,
            max_pages=20,
        )

    except (SettingsError, WCLV2ApiError) as exc:
        logger.print(f"One-guild schedule test failed: {exc}")
        logger.print("Use Settings and maintenance to set up/test the WCL v2 Client ID and Secret.")
        return

    reports = v2_reports_to_v1_meta(reports_raw)
    target_reports, unknown_reports, other_reports = filter_target_raid_reports(reports, config)
    # Reuse everything collected by this test in the normal batch scan.
    conn = connect_schedule_db(config)
    try:
        short_debug: list[str] = []
        hydrate_short_report_mythic_evidence(conn, target_reports, config, short_debug)
        nights = build_nights_from_reports(target_reports, config, tz_name)
        result = classify_schedule(
            guild=guild,
            realm=realm,
            region=region,
            rank=None,
            reports=target_reports,
            nights=nights,
            config=config,
            source="wcl_v2_single_guild_report_list_test",
        )
        upsert_report_cache(
            conn, guild, realm, region, start_ms, end_ms,
            reports, "wcl_v2_single_guild_report_list_test",
        )
        upsert_schedule_result(conn, result)
        replace_raid_nights(conn, guild, realm, region, nights)
    finally:
        conn.close()
    after_payload = client.test_query()
    after = after_payload.get("data", {}).get("rateLimitData", {})
    save_client_token(client)
    used = points_used_between(before, after)
    verdict = "YES - likely 2 days/week from available public logs" if result.is_likely_two_day else "NO - the available public logs do not match the 2-day thresholds"

    lines = [
        f"Guild: {guild}-{realm}-{region}",
        f"Season: {config.get('season', {}).get('name', '')}",
        f"Verdict: {verdict}",
        f"Target-raid reports: {len(target_reports)}",
        f"Unknown-zone reports excluded: {len(unknown_reports)}",
        f"Other-zone reports excluded: {len(other_reports)}",
        f"Raid nights found: {result.raid_nights_found}",
        f"Active weeks: {result.active_weeks}",
        f"Median raid nights/week: {result.inferred_days_per_week}",
        f"Average raid nights/active week: {result.average_raid_days_per_active_week}",
        f"First-month average raid nights/week: {result.first_month_average_raid_days}",
        f"Median logged-window hours/week: {result.logged_window_hours_per_week}",
        f"Common raid days: {result.inferred_raid_days or 'none'}",
        f"Reason: {result.reason}",
        f"WCL hourly limit: {after.get('limitPerHour', before.get('limitPerHour', 'unknown'))}",
        f"Points spent before: {before.get('pointsSpentThisHour', 'unknown')}",
        f"Points spent after: {after.get('pointsSpentThisHour', 'unknown')}",
        f"Points used by test: {used if used is not None else 'unknown (rate window reset or data unavailable)'}",
        f"Points reset in: {after.get('pointsResetIn', 'unknown')}",
        "Point measurement uses WCL rate-limit probes immediately before and after the report-list request.",
        "This verifies the schedule visible in available public logs; missing/private logs can lower the measured days.",
        "The report list, calculated schedule and raid nights were cached in SQLite for future runs.",
    ]

    output = Path("output/comparison/single_guild_schedule_test.txt")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for line in lines:
        logger.print(line)
    logger.print(f"Saved test details: {output}")


def rows_from_discovery(config: JsonDict, logger, conn=None) -> list[dict]:
    backup_cfg = config.get("comparison", {}).get("wowprogress_backup", {})
    scan_cfg = config.get("comparison", {}).get("schedule_scan", {})

    use_backup_source = bool(backup_cfg.get("use_as_schedule_scan_source", False)) or (
        scan_cfg.get("source_mode") == "wowprogress_backup_above_own_declared_1_2"
    )

    if use_backup_source:
        rows, meta = rows_from_wowprogress_backup_for_schedule_scan(config)
        if logger:
            logger.print("Using WoWProgress backup as schedule-scan source.")
            logger.print(f"Backup file: {meta.get('backup_file')}")
            logger.print(f"Backup rows in file: {meta.get('rows_in_backup')}")
            logger.print(
                f"Own guild used: {meta.get('own_guild') or 'not set'}-"
                f"{meta.get('own_realm') or 'not set'}-{meta.get('own_region') or 'not set'}"
            )
            logger.print(f"Own WoWProgress rank used: {meta.get('own_rank')}")
            logger.print(f"Schedule rows selected, including your guild: {meta.get('selected')}")
            if meta.get("own_reference_added"):
                logger.print("Your saved guild was added as a reference row even though it was absent from the backup CSV.")
            if meta.get("region_filter"):
                logger.print(f"Region filter: {meta.get('region_filter')}")
            else:
                logger.print("Region filter: world/all")
            if not meta.get("backup_file_exists"):
                logger.print("The local WoWProgress backup CSV is missing. Install the personal app package once; future updates will preserve it automatically.")
        return rows

    discovery = config.get("comparison", {}).get("discovery", {})
    saved_profile = get_guild_profile_from_settings()

    own_guild = (discovery.get("own_guild") or (saved_profile.name if saved_profile else "")).strip()
    own_realm = (discovery.get("own_realm") or (saved_profile.realm if saved_profile else "")).strip()
    own_region = (discovery.get("own_region") or (saved_profile.region if saved_profile else "EU")).strip().upper()

    discovered = discover_guilds(config, logger=logger)
    selected = select_guilds_around_own(
        guilds=discovered,
        own_guild=own_guild,
        own_realm=own_realm,
        own_region=own_region,
        above=int(discovery.get("guilds_above_own", 50)),
        below=int(discovery.get("guilds_below_own", 0)),
        max_used=int(discovery.get("max_discovered_guilds_used", 50)),
    )

    write_discovered_guilds(discovery.get("output_file", "output/comparison/discovered_guilds.csv"), selected)
    if conn is not None:
        upsert_discovered_guilds(conn, selected)

    return [
        {
            "guild": g.guild,
            "realm": g.realm,
            "region": g.region,
            "rank": g.rank,
            "endboss_kill_timestamp_ms": getattr(g, "endboss_kill_timestamp_ms", None),
            "endboss_kill_date": getattr(g, "endboss_kill_date", ""),
            "endboss_kill_source": getattr(g, "endboss_kill_source", ""),
        }
        for g in selected
    ]


def filter_unprocessed_guild_rows(conn, config: JsonDict, guild_rows: list[dict], logger) -> list[dict]:
    scan_cfg = config.get("comparison", {}).get("schedule_scan", {})
    if not scan_cfg.get("scan_only_unprocessed_guilds", True):
        return guild_rows

    skip_existing = bool(scan_cfg.get("skip_existing_schedule_results", True))
    if not skip_existing:
        return guild_rows

    retry_errors = bool(scan_cfg.get("retry_error_results", False))
    statuses = get_schedule_result_statuses(conn)

    filtered: list[dict] = []
    skipped = 0

    for row in guild_rows:
        if should_skip_existing_schedule_result(
            statuses,
            guild=row["guild"],
            realm=row["realm"],
            region=row["region"],
            retry_errors=retry_errors,
        ):
            skipped += 1
            continue
        filtered.append(row)

    if logger:
        logger.print(f"Skipped already schedule-scanned guilds: {skipped}")
        logger.print(f"Unprocessed guilds remaining in selected schedule-scan source: {len(filtered)}")

    return filtered


def write_schedule_results(path: str | Path, rows: list[ScheduleResult]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "guild",
        "realm",
        "region",
        "rank",
        "reports_found",
        "reports_used_for_schedule",
        "progression_cutoff_date",
        "progression_cutoff_source",
        "reports_after_cutoff_excluded",
        "raid_nights_found",
        "active_weeks",
        "inferred_days_per_week",
        "average_raid_days_per_active_week",
        "first_month_average_raid_days",
        "logged_window_hours_per_week",
        "inferred_hours_per_week",
        "inferred_raid_days",
        "is_likely_two_day",
        "candidate_needs_deep_time_review",
        "schedule_source",
        "schedule_confidence",
        "reason",
        "example_nights",
        "notes",
    ]

    with output.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def run_schedule_scan(config: JsonDict, logger) -> None:
    scan_cfg = config.get("comparison", {}).get("schedule_scan", {})
    if not scan_cfg.get("enabled", True):
        logger.print("Schedule scan is disabled in config.json.")
        return

    logger.print("Schedule scan selected.")
    logger.print("Scanning your guild plus declared 1-2 day guilds above you from the local WoWProgress backup list.")
    logger.print("Schedule verification uses WCL report lists; only otherwise-too-short reports need a cached fight-summary check.")

    backup_cfg = config.get("comparison", {}).get("wowprogress_backup", {})
    verification_enabled = bool(scan_cfg.get("actual_schedule_verification_enabled", True))
    declared_only_mode = not verification_enabled and bool(backup_cfg.get("declared_only_scan", False)) and not bool(backup_cfg.get("wcl_enrichment_enabled", False))
    parallel_enabled = bool(scan_cfg.get("parallel_schedule_scan", True))
    worker_count = max(1, int(scan_cfg.get("schedule_scan_workers", 4)))
    if declared_only_mode:
        logger.print("WCL report fetching is disabled for this declared-only run.")
    elif parallel_enabled and worker_count > 1:
        logger.print(f"Parallel WCL report-list fetching enabled: {worker_count} workers.")
    else:
        logger.print("Parallel fetching disabled; using single-threaded schedule scan.")

    conn = connect_schedule_db(config)

    guild_rows = rows_from_discovery(config, logger=logger, conn=conn)
    if not guild_rows:
        logger.print("No guild rows available for schedule scan.")
        conn.close()
        return

    if declared_only_mode:
        logger.print("Declared-only mode rebuilds the complete eligible guild list each run.")
    elif verification_enabled:
        logger.print("Actual-verification mode reprocesses every eligible guild, using cached report lists whenever available.")
    else:
        guild_rows = filter_unprocessed_guild_rows(conn, config, guild_rows, logger)
    if not guild_rows:
        logger.print("No unprocessed guilds left in the selected schedule-scan source.")
        logger.print("Increase the backup source limit or set skip_existing_schedule_results=false to rescan.")
        conn.close()
        return

    max_guilds = int(scan_cfg.get("max_guilds_per_run", 0))
    # Declared-only mode is a zero-WCL local operation, so legacy saved config
    # limits must not truncate the complete above-own guild list.
    process_all = verification_enabled or declared_only_mode or max_guilds <= 0
    selected_rows = guild_rows if process_all else guild_rows[:max_guilds]
    if process_all:
        logger.print(f"Processing all eligible guilds in this run: {len(selected_rows)}")
    else:
        logger.print(f"Processing this run's configured guild limit: {len(selected_rows)}/{len(guild_rows)}")
    output_file = scan_cfg.get("output_file", "output/comparison/schedule_scan.csv")
    debug_file = scan_cfg.get("debug_file", "output/comparison/schedule_scan_debug.txt")
    tz_name = scan_cfg.get("timezone") or config.get("season", {}).get("timezone", "Europe/London")
    season_config = config.get("season", {})
    start_ms, end_ms = season_range_ms(season_config)

    debug_lines: list[str] = []
    results: list[ScheduleResult] = []

    cache_hits = 0
    api_jobs: list[dict] = []

    backup_cfg = config.get("comparison", {}).get("wowprogress_backup", {})
    declared_only_scan = declared_only_mode
    backup_source_rows = bool(backup_cfg.get("use_as_schedule_scan_source", False)) or (
        scan_cfg.get("source_mode") == "wowprogress_backup_above_own_declared_1_2"
    )

    if backup_source_rows and declared_only_scan:
        logger.print("Declared-only WoWProgress backup scan enabled.")
        logger.print("WCL enrichment disabled: this run will use 0 WCL API calls/tokens.")

        for row in selected_rows:
            result = declared_only_backup_schedule_result(row, debug_lines)
            upsert_schedule_result(conn, result)
            replace_raid_nights(conn, row["guild"], row["realm"], row["region"], [])
            results.append(result)

        conn.close()
        results = sorted(results, key=lambda r: (r.rank is None, r.rank or 999999, r.guild.lower()))
        write_schedule_results(output_file, results)
        Path(debug_file).parent.mkdir(parents=True, exist_ok=True)

        backup_matches = len(results)
        debug_lines.append(
            f"RUN SUMMARY selected={len(selected_rows)} cache_hits=0 api_fetches=0 "
            f"parallel=False workers=0 public_wcl_results=0 wowprogress_backup_matches={backup_matches} errors=0 "
            f"declared_only_scan=True"
        )
        Path(debug_file).write_text("\n".join(debug_lines) + "\n", encoding="utf-8")

        logger.print(f"Schedule scan output: {output_file}")
        logger.print(f"Schedule scan debug: {debug_file}")
        logger.print("Cache hits this run: 0")
        logger.print("WCL API fetches this run: 0")
        logger.print(f"WoWProgress backup matches this run: {backup_matches}")
        logger.print("Public WCL results this run: 0")
        logger.print("Errors with no backup this run: 0")
        logger.print(f"Likely 2-day guilds in this run: {len(results)}/{len(results)}")
        return

    # Measure the whole verification pass, including the rare short-report fight
    # checks that may be needed even when every report list came from cache.
    rate_before: JsonDict = {}
    rate_after: JsonDict = {}
    meter_client = None
    try:
        meter_client = build_v2_client(config)
        rate_before = meter_client.test_query().get("data", {}).get("rateLimitData", {})
    except Exception as exc:
        logger.print(f"Could not read starting WCL point balance: {exc}")

    # Main thread cache pass. This avoids sending cached guilds to workers.
    for row in selected_rows:
        guild = row["guild"]
        realm = row["realm"]
        region = row["region"]
        cached = load_cached_or_legacy_reports_main_thread(
            conn=conn,
            config=config,
            guild=guild,
            realm=realm,
            region=region,
            debug_lines=debug_lines,
        )
        if cached is None:
            api_jobs.append(row)
            continue

        cache_hits += 1
        reports, source = cached
        logger.print(f"[cache {cache_hits}] {guild}-{realm}-{region}: {len(reports)} reports")
        try:
            result = classify_and_store_schedule_result(
                conn=conn,
                config=config,
                row=row,
                reports=reports,
                source=source,
                tz_name=tz_name,
                debug_lines=debug_lines,
            )
        except Exception as e:
            debug_lines.append(f"{guild}-{realm}-{region} :: ERROR :: {type(e).__name__}: {e}")
            result = backup_or_error_schedule_result(config, row, f"{type(e).__name__}: {e}", debug_lines)
            upsert_schedule_result(conn, result)
        results.append(result)

    # API pass. Workers fetch only; main thread stores cache/results.
    api_fetches = 0
    if api_jobs:
        logger.print(f"WCL API fetches needed this run: {len(api_jobs)}")
    else:
        logger.print("No report-list fetches needed; all selected guild report lists came from cache.")

    if api_jobs and parallel_enabled and worker_count > 1:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_map = {
                executor.submit(fetch_guild_reports_api_worker, row, config): row
                for row in api_jobs
            }
            completed = 0
            for future in as_completed(future_map):
                completed += 1
                row = future_map[future]
                guild = row["guild"]
                realm = row["realm"]
                region = row["region"]

                try:
                    job = future.result()
                except Exception as e:
                    debug_lines.append(f"{guild}-{realm}-{region} :: WORKER ERROR :: {type(e).__name__}: {e}")
                    result = backup_or_error_schedule_result(config, row, f"{type(e).__name__}: {e}", debug_lines)
                    upsert_schedule_result(conn, result)
                    results.append(result)
                    logger.print(f"[api {completed}/{len(api_jobs)}] ERROR {guild}-{realm}-{region}")
                    continue

                debug_lines.extend(job.debug_lines)

                if job.error:
                    result = backup_or_error_schedule_result(config, row, job.error, debug_lines)
                    upsert_schedule_result(conn, result)
                    results.append(result)
                    logger.print(f"[api {completed}/{len(api_jobs)}] ERROR {guild}-{realm}-{region}")
                    continue

                api_fetches += 1
                upsert_report_cache(conn, guild, realm, region, start_ms, end_ms, job.reports, "wcl_v2_api_parallel")
                debug_lines.append(f"{guild}-{realm}-{region} :: reports api fetch stored in sqlite :: {len(job.reports)} reports")

                try:
                    result = classify_and_store_schedule_result(
                        conn=conn,
                        config=config,
                        row=row,
                        reports=job.reports,
                        source="wcl_v2_api_parallel",
                        tz_name=tz_name,
                        debug_lines=debug_lines,
                    )
                except Exception as e:
                    debug_lines.append(f"{guild}-{realm}-{region} :: ERROR :: {type(e).__name__}: {e}")
                    result = backup_or_error_schedule_result(config, row, f"{type(e).__name__}: {e}", debug_lines)
                    upsert_schedule_result(conn, result)

                results.append(result)
                logger.print(f"[api {completed}/{len(api_jobs)}] {guild}-{realm}-{region}: {len(job.reports)} reports")

    elif api_jobs:
        # Single-threaded fallback path.
        client = build_v2_client(config)
        for idx, row in enumerate(api_jobs, start=1):
            guild = row["guild"]
            realm = row["realm"]
            region = row["region"]
            logger.print(f"[api {idx}/{len(api_jobs)}] Cheap report-list candidate scan: {guild}-{realm}-{region}")

            try:
                reports_raw = client.fetch_guild_reports(
                    guild_name=guild,
                    guild_server_slug=slugify_realm(realm),
                    guild_server_region=region.upper(),
                    start_time=start_ms,
                    end_time=end_ms,
                    max_pages=20,
                )
                reports = v2_reports_to_v1_meta(reports_raw)
                api_fetches += 1
                upsert_report_cache(conn, guild, realm, region, start_ms, end_ms, reports, "wcl_v2_api")
                debug_lines.append(f"{guild}-{realm}-{region} :: reports api fetch stored in sqlite :: {len(reports)} reports")

                result = classify_and_store_schedule_result(
                    conn=conn,
                    config=config,
                    row=row,
                    reports=reports,
                    source="wcl_v2_api",
                    tz_name=tz_name,
                    debug_lines=debug_lines,
                )

            except Exception as e:
                debug_lines.append(f"{guild}-{realm}-{region} :: ERROR :: {type(e).__name__}: {e}")
                result = backup_or_error_schedule_result(config, row, f"{type(e).__name__}: {e}", debug_lines)
                upsert_schedule_result(conn, result)

            results.append(result)

        save_client_token(client)

    if meter_client is not None:
        try:
            rate_after = meter_client.test_query().get("data", {}).get("rateLimitData", {})
            save_client_token(meter_client)
        except Exception as exc:
            logger.print(f"Could not read ending WCL point balance: {exc}")

    conn.close()

    # Keep output ordered by rank where possible, not by worker completion order.
    results = sorted(results, key=lambda r: (r.rank is None, r.rank or 999999, r.guild.lower()))

    write_schedule_results(output_file, results)
    Path(debug_file).parent.mkdir(parents=True, exist_ok=True)
    backup_matches = sum(1 for r in results if r.schedule_source == "wowprogress_screenshot_backup_declared_only")
    public_wcl_measured = sum(1 for r in results if r.schedule_source == "wcl_report_list_candidate_filter")
    unverified_count = sum(1 for r in results if r.schedule_confidence == "unverified")
    error_count = sum(1 for r in results if r.schedule_confidence == "error")
    debug_lines.append(
        f"RUN SUMMARY selected={len(selected_rows)} cache_hits={cache_hits} api_fetches={api_fetches} "
        f"parallel={parallel_enabled and worker_count > 1} workers={worker_count} "
        f"public_wcl_results={public_wcl_measured} wowprogress_backup_matches={backup_matches} errors={error_count}"
    )
    Path(debug_file).write_text("\n".join(debug_lines) + "\n", encoding="utf-8")

    likely_count = sum(1 for r in results if r.is_likely_two_day)
    logger.print(f"Schedule scan output: {output_file}")
    logger.print(f"Schedule scan debug: {debug_file}")
    logger.print(f"Cache hits this run: {cache_hits}")
    logger.print(f"WCL API fetches this run: {api_fetches}")
    if rate_before or rate_after:
        measured_points = points_used_between(rate_before, rate_after)
        logger.print(f"WCL points before: {rate_before.get('pointsSpentThisHour', 'unknown')}")
        logger.print(f"WCL points after: {rate_after.get('pointsSpentThisHour', 'unknown')}")
        logger.print(f"WCL points used this run: {measured_points if measured_points is not None else 'unknown'}")
    logger.print(f"WoWProgress backup matches this run: {backup_matches}")
    logger.print(f"Public WCL results this run: {public_wcl_measured}")
    logger.print(f"Guilds not verifiable from public WCL reports: {unverified_count}")
    logger.print(f"Errors with no backup this run: {error_count}")
    logger.print(f"Likely 2-day guilds in this run: {likely_count}/{len(results)}")
