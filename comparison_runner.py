from __future__ import annotations

from pathlib import Path
from typing import Any
from datetime import datetime, timezone
import csv
import time

import pandas as pd

from guild_discovery import (
    discover_guilds,
    discovered_to_comparison_rows,
    select_guilds_around_own,
    write_discovered_guilds,
)

from comparison_db import (
    connect_database,
    guild_key,
    load_all_summaries,
    now_utc,
    save_boss_summaries,
    save_guild_summary,
    summary_exists,
    upsert_guild,
)
from exporter import build_summaries
from guild_fetcher import (
    build_zone_lookup,
    choose_reports_by_day_or_session,
    count_mythic_pulls,
    load_or_fetch_guild_reports,
    load_or_fetch_zones,
    report_code,
    report_date,
    report_zone_id,
    report_zone_name,
    resolve_midnight_zone_ids,
    season_range_ms,
    shortlist_reports_for_deep_inspection,
)
from processor import classify_reports, rows_to_dicts, canonical_boss_name, boss_allowed
from settings_manager import get_global_settings_dir, get_guild_profile_from_settings
from cache_manager import ReportCache
from wcl_api import WCLApiError
from v2_report_tools import fetch_guild_reports_v2


JsonDict = dict[str, Any]


def database_path(config: JsonDict) -> Path:
    comp = config.get("comparison", {})
    filename = comp.get("database_file", "comparison.sqlite")
    if comp.get("database_location", "global_app_data") == "global_app_data":
        return get_global_settings_dir() / "database" / filename
    return Path(filename)


def load_comparison_guilds(path: str) -> list[JsonDict]:
    guilds: list[JsonDict] = []

    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(row for row in f if not row.lstrip().startswith("#"))
        for row in reader:
            guild = (row.get("guild") or "").strip()
            realm = (row.get("realm") or "").strip()
            region = (row.get("region") or "EU").strip().upper()
            if not guild or not realm:
                continue

            def parse_float(value: str | None) -> float | None:
                if value is None or str(value).strip() == "":
                    return None
                try:
                    return float(value)
                except ValueError:
                    return None

            guilds.append(
                {
                    "guild": guild,
                    "realm": realm,
                    "region": region,
                    "declared_days_per_week": parse_float(row.get("declared_days_per_week")),
                    "declared_hours_per_week": parse_float(row.get("declared_hours_per_week")),
                    "schedule_source": (row.get("schedule_source") or "").strip() or None,
                }
            )

    return guilds


def merge_guild_rows(primary: list[JsonDict], extra: list[JsonDict]) -> list[JsonDict]:
    merged: list[JsonDict] = []
    seen: set[tuple[str, str, str]] = set()

    for row in primary + extra:
        key = (
            row.get("guild", "").strip().lower(),
            row.get("realm", "").strip().lower(),
            row.get("region", "EU").strip().upper(),
        )
        if not key[0] or not key[1] or key in seen:
            continue
        seen.add(key)
        merged.append(row)

    return merged


def infer_schedule_from_rows(fight_df: pd.DataFrame) -> tuple[float | None, float | None, str]:
    if fight_df.empty:
        return None, None, "none"

    df = fight_df.copy()
    df["date_dt"] = pd.to_datetime(df["raid_date_utc"], errors="coerce")
    df = df.dropna(subset=["date_dt"])

    if df.empty:
        return None, None, "none"

    df["iso_year"] = df["date_dt"].dt.isocalendar().year
    df["iso_week"] = df["date_dt"].dt.isocalendar().week

    night_summary = (
        df.groupby(["raid_date_utc", "iso_year", "iso_week"])
        .agg(window_hours=("window_segment_hours", "sum"))
        .reset_index()
    )

    weekly = (
        night_summary.groupby(["iso_year", "iso_week"])
        .agg(days=("raid_date_utc", "nunique"), hours=("window_hours", "sum"))
        .reset_index()
    )

    active_weekly = weekly[weekly["days"] > 0]
    if active_weekly.empty:
        return None, None, "none"

    return (
        round(float(active_weekly["days"].median()), 2),
        round(float(active_weekly["hours"].median()), 2),
        "inferred_from_logs",
    )


def count_allowed_boss_pulls(report_data: JsonDict, boss_filter: JsonDict, mythic_difficulty: int) -> dict[str, int]:
    counts: dict[str, int] = {}
    for fight in report_data.get("fights", []):
        if fight.get("difficulty") != mythic_difficulty or not fight.get("encounterID"):
            continue
        boss_name = canonical_boss_name(fight.get("name", "Unknown"))
        if not boss_allowed(boss_name, boss_filter):
            continue
        counts[boss_name] = counts.get(boss_name, 0) + 1
    return counts


def process_single_guild(
    guild_row: JsonDict,
    config: JsonDict,
    client,
    cache: ReportCache,
    logger,
) -> tuple[JsonDict | None, list[JsonDict], list[JsonDict]]:
    mythic_difficulty = int(config.get("mythic_difficulty", 5))
    minimum_fight_seconds = int(config.get("minimum_fight_seconds", 10))
    season_config = config.get("season", {})
    selection_config = config.get("report_selection", {})
    boss_filter_config = config.get("boss_filter", {})
    zone_config = config.get("midnight_raid_zones", {})

    start_ms, end_ms = season_range_ms(season_config)
    tz_name = season_config.get("timezone", "Europe/London")

    guild = guild_row["guild"]
    realm = guild_row["realm"]
    region = guild_row["region"]

    def fetch_from_api(code: str) -> JsonDict:
        return client.fetch_report_fights(code, mythic_difficulty=mythic_difficulty)

    try:
        zones, _ = load_or_fetch_zones(client, force_refresh=False)
        zone_lookup = build_zone_lookup(zones)
        midnight_zone_ids = resolve_midnight_zone_ids(
            zones=zones,
            configured_ids=zone_config.get("zone_ids", []),
            name_contains=zone_config.get("name_contains", ["Midnight"]),
        )

        report_discovery_mode = config.get("comparison", {}).get("report_discovery_mode", "v1")
        if report_discovery_mode in {"v2", "v2_with_v1_fallback"} and config.get("api", {}).get("v2", {}).get("use_for_guild_reports", False):
            try:
                guild_reports = fetch_guild_reports_v2(config, guild, realm, region)
                guild_reports_source = "v2"
            except Exception as v2_error:
                if report_discovery_mode == "v2":
                    raise WCLApiError(f"v2 guild report discovery failed: {v2_error}") from v2_error
                logger.print(f"  v2 report discovery failed for {guild}-{realm}-{region}; falling back to v1: {v2_error}")
                guild_reports, guild_reports_source = load_or_fetch_guild_reports(
                    client=client,
                    guild_name=guild,
                    realm=realm,
                    region=region,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    force_refresh=False,
                )
        else:
            guild_reports, guild_reports_source = load_or_fetch_guild_reports(
                client=client,
                guild_name=guild,
                realm=realm,
                region=region,
                start_ms=start_ms,
                end_ms=end_ms,
                force_refresh=False,
            )
    except WCLApiError as e:
        return (
            {
                "guild": guild,
                "realm": realm,
                "region": region,
                "status": f"failed_fetch_guild_reports: {e}",
            },
            [],
            [],
        )

    candidates = []
    report_audit: list[JsonDict] = []
    minimum_pulls = int(selection_config.get("minimum_mythic_pulls", 1))

    deep_reports, metadata_reasons = shortlist_reports_for_deep_inspection(
        reports=guild_reports,
        tz_name=tz_name,
        midnight_zone_ids=midnight_zone_ids,
        name_contains=zone_config.get("name_contains", ["Midnight"]),
        zone_lookup=zone_lookup,
        selection_config=selection_config,
    )
    deep_codes = {report_code(meta) for meta in deep_reports}
    cached_codes: set[str] = set()
    for meta in guild_reports:
        code = report_code(meta)
        if not code:
            continue
        try:
            if cache.load_report(code) is not None:
                cached_codes.add(code)
        except Exception:
            # Invalid legacy cache files are ignored here and will be fetched
            # again only when selected by the metadata first pass.
            pass
    inspect_codes = deep_codes | cached_codes
    logger.print(
        f"  metadata-first: {len(guild_reports)} reports -> {len(deep_codes)} detailed candidates "
        f"({len(deep_codes - cached_codes)} new request(s))"
    )

    for meta in guild_reports:
        code = report_code(meta)
        if not code:
            continue

        local_date = report_date(meta, tz_name)

        if code not in inspect_codes:
            report_audit.append(
                {
                    "guild": guild,
                    "realm": realm,
                    "region": region,
                    "date": local_date,
                    "report_code": code,
                    "title": meta.get("title", ""),
                    "zone_id": report_zone_id(meta),
                    "zone_name": report_zone_name(meta, zone_lookup),
                    "tracked_boss_pulls": 0,
                    "tracked_boss_breakdown": "",
                    "candidate": False,
                    "source": metadata_reasons.get(code, "skipped_by_metadata_first_pass"),
                }
            )
            continue

        try:
            report_data, source = cache.fetch_or_load_report(
                report_code=code,
                fetch_func=fetch_from_api,
                force_refresh=False,
                query={"difficulty": mythic_difficulty, "mode": "comparison"},
            )

            allowed_boss_counts = count_allowed_boss_pulls(report_data, boss_filter_config, mythic_difficulty)
            allowed_total_pulls = sum(allowed_boss_counts.values())
            boss_count_text = "; ".join(f"{boss}: {pulls}" for boss, pulls in sorted(allowed_boss_counts.items()))

            if allowed_total_pulls >= minimum_pulls:
                candidates.append((meta, report_data, allowed_total_pulls, local_date))

            report_audit.append(
                {
                    "guild": guild,
                    "realm": realm,
                    "region": region,
                    "date": local_date,
                    "report_code": code,
                    "title": meta.get("title", report_data.get("title", "")),
                    "zone_id": report_zone_id(meta),
                    "zone_name": report_zone_name(meta, zone_lookup),
                    "tracked_boss_pulls": allowed_total_pulls,
                    "tracked_boss_breakdown": boss_count_text,
                    "candidate": allowed_total_pulls >= minimum_pulls,
                    "source": source,
                }
            )

        except Exception as e:
            report_audit.append(
                {
                    "guild": guild,
                    "realm": realm,
                    "region": region,
                    "date": local_date,
                    "report_code": code,
                    "title": meta.get("title", ""),
                    "tracked_boss_pulls": 0,
                    "tracked_boss_breakdown": "",
                    "candidate": False,
                    "source": f"failed: {e}",
                }
            )

    chosen = choose_reports_by_day_or_session(
        report_candidates=candidates,
        tz_name=tz_name,
        zone_lookup=zone_lookup,
        selection_config=selection_config,
    )

    chosen_codes = {item.code for item in chosen}
    reports = [
        report_data
        for meta, report_data, pulls, local_date in candidates
        if report_code(meta) in chosen_codes
    ]

    if not reports:
        return (
            {
                "guild": guild,
                "realm": realm,
                "region": region,
                "status": "no_selected_reports",
            },
            [],
            report_audit,
        )

    rows = classify_reports(
        reports,
        mythic_difficulty=mythic_difficulty,
        minimum_fight_seconds=minimum_fight_seconds,
        boss_filter=boss_filter_config,
        time_config=config.get("time_calculation", {}),
    )
    row_dicts = rows_to_dicts(rows)
    fight_df = pd.DataFrame(row_dicts)

    if fight_df.empty:
        return (
            {
                "guild": guild,
                "realm": realm,
                "region": region,
                "status": "no_fight_rows_after_classification",
            },
            [],
            report_audit,
        )

    _, overall_summary, boss_summary, _, _ = build_summaries(fight_df)
    overall = overall_summary.iloc[0].to_dict()

    inferred_days, inferred_hours, inferred_source = infer_schedule_from_rows(fight_df)

    declared_days = guild_row.get("declared_days_per_week")
    declared_hours = guild_row.get("declared_hours_per_week")
    declared_source = guild_row.get("schedule_source")

    schedule_days = declared_days if declared_days is not None else inferred_days
    schedule_hours = declared_hours if declared_hours is not None else inferred_hours
    final_source = declared_source or inferred_source

    complete_tier = int((boss_summary["kill_date"].astype(str) != "").sum() >= int(config.get("midnight_raid_zones", {}).get("total_bosses", 9)))

    summary = {
        "guild": guild,
        "realm": realm,
        "region": region,
        "declared_days_per_week": declared_days,
        "declared_hours_per_week": declared_hours,
        "schedule_source": declared_source,
        "inferred_days_per_week": inferred_days,
        "inferred_hours_per_week": inferred_hours,
        "inferred_schedule_source": inferred_source,
        "schedule_days_per_week": schedule_days,
        "schedule_hours_per_week": schedule_hours,
        "final_schedule_source": final_source,
        "active_weeks": int(fight_df.assign(date_dt=pd.to_datetime(fight_df["raid_date_utc"])).date_dt.dt.isocalendar().week.nunique()),
        "bosses_killed": int((boss_summary["kill_date"].astype(str) != "").sum()),
        "complete_tier": complete_tier,
        "status": "ok",
    }
    summary.update(overall)

    return summary, boss_summary.to_dict(orient="records"), report_audit


def build_comparison_outputs(conn, output_folder: Path, config: JsonDict) -> None:
    output_folder.mkdir(parents=True, exist_ok=True)

    summaries = load_all_summaries(conn)
    df = pd.DataFrame(summaries)

    if df.empty:
        df.to_csv(output_folder / "comparison_summary.csv", index=False)
        return

    target_days = float(config.get("comparison", {}).get("target_days_per_week", 2))
    target_hours = float(config.get("comparison", {}).get("target_hours_per_week", 6))
    hours_tolerance = float(config.get("comparison", {}).get("hours_tolerance", 1.5))
    region_filter = (config.get("comparison", {}).get("region_filter") or "").upper()

    if region_filter:
        df["is_target_region"] = df["region"].str.upper() == region_filter
    else:
        df["is_target_region"] = True

    df["is_target_schedule"] = (
        (df["schedule_days_per_week"].round(1) == round(target_days, 1))
        & ((df["schedule_hours_per_week"] - target_hours).abs() <= hours_tolerance)
    )

    metric_cols = [
        "reclear_tax_percent",
        "pull_uptime_percent",
        "pulls_per_hour",
        "progression_window_hours",
        "reclear_window_hours",
        "total_window_hours",
        "reclear_wipes",
    ]

    peer_df = df[(df["status"] == "ok") & (df["is_target_region"]) & (df["is_target_schedule"])].copy()

    for col in metric_cols:
        if col not in df.columns:
            continue
        if not peer_df.empty:
            df[f"{col}_peer_percentile"] = df[col].rank(pct=True) * 100
            peer_median = peer_df[col].median()
            df[f"{col}_peer_median"] = peer_median
            df[f"{col}_vs_peer_median"] = df[col] - peer_median
        else:
            df[f"{col}_peer_percentile"] = None
            df[f"{col}_peer_median"] = None
            df[f"{col}_vs_peer_median"] = None

    df.to_csv(output_folder / "comparison_summary.csv", index=False)

    peer_summary = pd.DataFrame(
        [
            {
                "peer_count": len(peer_df),
                "region_filter": region_filter,
                "target_days_per_week": target_days,
                "target_hours_per_week": target_hours,
                "hours_tolerance": hours_tolerance,
                "median_reclear_tax_percent": peer_df["reclear_tax_percent"].median() if not peer_df.empty else None,
                "median_pull_uptime_percent": peer_df["pull_uptime_percent"].median() if not peer_df.empty else None,
                "median_pulls_per_hour": peer_df["pulls_per_hour"].median() if not peer_df.empty else None,
            }
        ]
    )
    peer_summary.to_csv(output_folder / "comparison_peer_summary.csv", index=False)


def run_comparison_mode(config: JsonDict, client, cache: ReportCache, logger) -> None:
    comp = config.get("comparison", {})
    guilds_file = comp.get("guilds_file", "comparison_guilds.csv")

    manual_guilds: list[JsonDict] = []
    if bool(comp.get("use_comparison_guilds_file", True)):
        manual_guilds = load_comparison_guilds(guilds_file)

    discovered_rows: list[JsonDict] = []
    discovery = comp.get("discovery", {})
    if bool(comp.get("auto_discover_guilds", False)) and bool(discovery.get("enabled", False)):
        saved_profile = get_guild_profile_from_settings()

        own_guild = (discovery.get("own_guild") or (saved_profile.name if saved_profile else "")).strip()
        own_realm = (discovery.get("own_realm") or (saved_profile.realm if saved_profile else "")).strip()
        own_region = (discovery.get("own_region") or (saved_profile.region if saved_profile else "EU")).strip().upper()

        logger.print("Automatic WCL guild discovery enabled.")
        logger.print(f"Discovery target: guilds above {own_guild}-{own_realm}-{own_region}" if own_guild and own_realm else "Discovery target guild not set/found; using first discovered guilds.")

        discovered = discover_guilds(config, logger=logger)
        selected_discovered = select_guilds_around_own(
            guilds=discovered,
            own_guild=own_guild,
            own_realm=own_realm,
            own_region=own_region,
            above=int(discovery.get("guilds_above_own", 50)),
            below=int(discovery.get("guilds_below_own", 0)),
            max_used=int(discovery.get("max_discovered_guilds_used", 50)),
        )
        write_discovered_guilds(discovery.get("output_file", "output/comparison/discovered_guilds.csv"), selected_discovered)
        discovered_rows = discovered_to_comparison_rows(selected_discovered)

        logger.print(f"Discovered guilds parsed: {len(discovered)}")
        logger.print(f"Discovered guilds selected for comparison: {len(discovered_rows)}")
        logger.print(f"Discovery output: {discovery.get('output_file', 'output/comparison/discovered_guilds.csv')}")

    guilds = merge_guild_rows(manual_guilds, discovered_rows)

    if not guilds:
        logger.print("No comparison guilds available.")
        logger.print("Either add rows to comparison_guilds.csv or check WCL discovery output.")
        return

    db_path = database_path(config)
    conn = connect_database(db_path)
    logger.print(f"Comparison database: {db_path}")
    logger.print(f"Guilds in comparison list: {len(guilds)}")

    max_guilds = int(comp.get("max_guilds_per_run", 10))
    delay = float(comp.get("delay_between_guilds_seconds", 0.25))
    use_cached = bool(comp.get("use_cached_guild_if_available", True))
    refresh_existing = bool(comp.get("refresh_existing_guilds", False))

    processed = 0

    for guild_row in guilds:
        if processed >= max_guilds:
            logger.print(f"Reached max_guilds_per_run={max_guilds}. Run again to process more.")
            break

        key = upsert_guild(
            conn,
            guild_row["guild"],
            guild_row["realm"],
            guild_row["region"],
            guild_row.get("declared_days_per_week"),
            guild_row.get("declared_hours_per_week"),
            guild_row.get("schedule_source"),
        )

        if use_cached and not refresh_existing and summary_exists(conn, key):
            logger.print(f"Skipping cached guild: {guild_row['guild']}-{guild_row['realm']}-{guild_row['region']}")
            continue

        logger.print(f"Processing guild: {guild_row['guild']}-{guild_row['realm']}-{guild_row['region']}")
        summary, boss_rows, audit_rows = process_single_guild(guild_row, config, client, cache, logger)

        if summary is None:
            logger.print("  No summary produced.")
            continue

        summary["guild_key"] = key
        summary["last_processed_at_utc"] = now_utc()

        save_guild_summary(conn, summary)
        save_boss_summaries(conn, key, boss_rows)

        processed += 1
        logger.print(f"  Status: {summary.get('status')}")
        logger.print(f"  Reclear tax: {summary.get('reclear_tax_percent')}")

        if delay:
            time.sleep(delay)

    output_folder = Path(comp.get("output_folder", "output/comparison"))
    build_comparison_outputs(conn, output_folder, config)
    logger.print(f"Comparison outputs written to: {output_folder}")
