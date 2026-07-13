from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from bootstrap import ensure_dependencies


JsonDict = dict[str, Any]


def load_config_early(path: str = "config.json") -> JsonDict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_version() -> str:
    try:
        with open("version.txt", "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return "unknown"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="WCL Reclear Tracker")
    parser.add_argument("--check-settings", action="store_true", help="Check Python packages, WCL key, cache, and reports.")
    parser.add_argument("--reset-key", action="store_true", help="Remove the saved Warcraft Logs API key.")
    parser.add_argument("--reset-guild", action="store_true", help="Remove the saved guild profile.")
    parser.add_argument("--clear-cache", action="store_true", help="Delete all globally cached Warcraft Logs data.")
    parser.add_argument("--clear-output", action="store_true", help="Delete old CSV/XLSX output files and exit.")
    parser.add_argument("--guild", help="Guild name to use for this run.")
    parser.add_argument("--realm", help="Realm name to use for this run.")
    parser.add_argument("--region", default=None, help="Region to use for this run, e.g. EU or US.")
    parser.add_argument("--save-guild", action="store_true", help="Save the --guild/--realm/--region values globally.")
    parser.add_argument("--configure-guild", action="store_true", help="Save guild details without running the tracker.")
    parser.add_argument("--comparison", action="store_true", help="Run comparison mode using comparison_guilds.csv.")
    parser.add_argument("--setup-v2", action="store_true", help="Set up/test Warcraft Logs v2 OAuth Client ID/Secret.")
    parser.add_argument("--reset-v2", action="store_true", help="Remove saved Warcraft Logs v2 OAuth credentials and cached token.")
    parser.add_argument("--test-v2-reports", action="store_true", help="Test WCL v2 guild report discovery for the saved guild.")
    parser.add_argument("--test-discovery", action="store_true", help="Test guild discovery only without fetching WCL reports.")
    parser.add_argument("--schedule-scan", action="store_true", help="Scan discovered guild pages for declared raid schedules only.")
    parser.add_argument("--test-schedule-guild", action="store_true", help="Test actual raid days and WCL point cost for one guild using report metadata.")
    parser.add_argument("--test-wowprogress", action="store_true", help="Test WoWProgress 1-2 raids/week discovery only.")
    return parser


def resolve_cache_folder(cache_config: JsonDict, get_global_cache_dir_func) -> str:
    cache_location = cache_config.get("location", "local_program_folder")

    if cache_location == "global_app_data":
        cache_subfolder = cache_config.get("fights_folder") or cache_config.get("folder", "reports")
        return str(get_global_cache_dir_func(cache_subfolder))

    return str(cache_config.get("folder", "cache/reports"))


def clear_output_files(output_folder: str) -> tuple[int, Path]:
    output_path = Path(output_folder)
    if not output_path.exists():
        return 0, output_path

    removed = 0
    for old_file in output_path.iterdir():
        if old_file.is_file() and old_file.suffix.lower() in {".csv", ".xlsx", ".txt", ".log"}:
            old_file.unlink()
            removed += 1

    return removed, output_path


def main() -> None:
    args = build_parser().parse_args()
    config = load_config_early()

    # Settings and housekeeping use only the Python standard library. Handle
    # them before dependency setup so changing a guild/key never triggers a
    # pandas/openpyxl installation or starts the tracker.
    if any(
        [
            args.configure_guild,
            args.reset_key,
            args.reset_v2,
            args.reset_guild,
            args.clear_cache,
            args.clear_output,
            args.check_settings,
        ]
    ):
        from settings_manager import (
            GuildProfile,
            clear_all_global_caches,
            reset_saved_api_key,
            reset_saved_guild_profile,
            reset_saved_v2_credentials,
            save_guild_profile_to_settings,
        )

        if args.configure_guild:
            if not args.guild or not args.realm:
                print("Guild name and realm are required.")
                return
            profile = GuildProfile(
                name=args.guild.strip(),
                realm=args.realm.strip(),
                region=(args.region or config.get("guild", {}).get("region", "EU")).strip().upper(),
            )
            path = save_guild_profile_to_settings(profile)
            print(f"Saved guild profile: {profile.name}-{profile.realm}-{profile.region}")
            print(f"Settings file: {path}")
            return

        if args.reset_key:
            removed = reset_saved_api_key()
            print("Saved Warcraft Logs API key removed." if removed else "No saved Warcraft Logs API key was found.")
            print("The main tracker will ask for a new key when it next needs one.")
            return

        if args.reset_v2:
            removed = reset_saved_v2_credentials()
            print("Saved Warcraft Logs v2 credentials removed." if removed else "No saved WCL v2 credentials were found.")
            return

        if args.reset_guild:
            removed = reset_saved_guild_profile()
            print("Saved guild profile removed." if removed else "No saved guild profile was found.")
            print("The main tracker will ask for a guild on its next run.")
            return

        if args.clear_cache:
            for label, removed, path in clear_all_global_caches():
                print(f"{label}: {path}")
                print("  cleared." if removed else "  nothing found.")
            return

        if args.clear_output:
            removed_count, output_path = clear_output_files(config.get("output_folder", "output"))
            print(f"Output folder: {output_path}")
            print(f"Deleted {removed_count} old output file(s).")
            return

        if args.check_settings:
            from diagnostics import run_check_settings

            run_check_settings(config)
            return

    ensure_dependencies(auto_install=bool(config.get("auto_install_dependencies", True)))

    from dotenv import load_dotenv

    from cache_manager import CacheError, ReportCache
    from exporter import export_outputs
    from guild_fetcher import (
        build_zone_lookup,
        choose_one_report_per_day,
        choose_reports_by_day_or_session,
        count_mythic_pulls,
        load_or_fetch_guild_reports,
        load_or_fetch_zones,
        report_code,
        report_date,
        report_matches_midnight_zone,
        report_zone_id,
        report_zone_name,
        resolve_midnight_zone_ids,
        season_range_ms,
        shortlist_reports_for_deep_inspection,
    )
    from logger_utils import RunLogger
    from processor import classify_reports, load_report_codes, rows_to_dicts
    from settings_manager import (
        GuildProfile,
        SettingsError,
        get_global_cache_dir,
        clear_global_cache,
        resolve_guild_profile,
        resolve_wcl_api_key,
        save_guild_profile_to_settings,
    )
    from wcl_api import WCLApiError, WCLClient
    from comparison_runner import run_comparison_mode
    from v2_setup import run_v2_setup_test
    from v2_report_tools import run_v2_report_test
    from discovery_test import run_discovery_test
    from schedule_scan import run_schedule_scan, run_single_guild_schedule_test
    from wowprogress_test import run_wowprogress_test

    load_dotenv()

    log_config = config.get("logs", {})
    logger = RunLogger(
        enabled=bool(log_config.get("enabled", True)),
        folder=log_config.get("folder", "logs"),
        latest_log=log_config.get("latest_log", "latest_run.txt"),
    )

    version = load_version()
    logger.print(f"WCL Reclear Tracker v{version}")
    logger.print()

    if args.setup_v2:
        run_v2_setup_test(config, logger)
        return

    if args.test_discovery:
        run_discovery_test(config, logger)
        return

    if args.schedule_scan:
        run_schedule_scan(config, logger)
        return

    if args.test_schedule_guild:
        run_single_guild_schedule_test(
            config,
            logger,
            guild=args.guild,
            realm=args.realm,
            region=args.region,
        )
        return

    if args.test_wowprogress:
        run_wowprogress_test(config, logger)
        return

    mythic_difficulty = int(config.get("mythic_difficulty", 5))
    minimum_fight_seconds = int(config.get("minimum_fight_seconds", 10))
    output_folder = config.get("output_folder", "output")

    cache_config = config.get("cache", {})
    cache_enabled = bool(cache_config.get("enabled", True))
    cache_folder = resolve_cache_folder(cache_config, get_global_cache_dir)
    force_refresh = bool(cache_config.get("force_refresh", False))
    if bool(cache_config.get("clear_all_cache_on_start", False)):
        removed, cache_root = clear_global_cache()
        logger.print(f"clear_all_cache_on_start enabled. Cache folder: {cache_root}")
        logger.print("Global Warcraft Logs cache deleted before run." if removed else "No global cache folder was found before run.")
    cache_schema_version = int(cache_config.get("schema_version", 1))
    supported_cache_schemas = cache_config.get("supported_schema_versions", [cache_schema_version])

    settings_config = config.get("settings", {})
    use_global_settings = bool(settings_config.get("use_global_settings", True))

    try:
        api_key = resolve_wcl_api_key(
            env_api_key=os.getenv("WCL_API_KEY"),
            use_global_settings=use_global_settings,
        )
    except SettingsError as e:
        logger.print(f"Settings error: {e}")
        return

    logger.print(f"Cache enabled: {cache_enabled}")
    logger.print(f"Cache folder: {cache_folder}")
    logger.print(f"Force refresh: {force_refresh}")

    client = WCLClient(api_key.api_key)
    cache = ReportCache(
        folder=cache_folder,
        enabled=cache_enabled,
        schema_version=cache_schema_version,
        supported_schema_versions=supported_cache_schemas,
    )

    if args.test_v2_reports:
        run_v2_report_test(config, client, logger)
        return

    if args.comparison:
        logger.print("Comparison mode selected.")
        logger.print("This uses local SQLite caching to avoid repeatedly re-fetching already processed guilds.")
        run_comparison_mode(config, client, cache, logger)
        return

    reports: list[JsonDict] = []
    selected_reports: list[dict] = []
    warnings: list[dict] = []
    report_audit: list[dict] = []
    report_boss_audit: list[dict] = []

    def fetch_from_api(code: str) -> JsonDict:
        return client.fetch_report_fights(code, mythic_difficulty=mythic_difficulty)

    def count_allowed_boss_pulls(report_data: JsonDict, boss_filter: JsonDict) -> dict[str, int]:
        from processor import canonical_boss_name, boss_allowed

        counts: dict[str, int] = {}
        for fight in report_data.get("fights", []):
            if fight.get("difficulty") != mythic_difficulty or not fight.get("encounterID"):
                continue
            boss_name = canonical_boss_name(fight.get("name", "Unknown"))
            if not boss_allowed(boss_name, boss_filter):
                continue
            counts[boss_name] = counts.get(boss_name, 0) + 1
        return counts

    def count_all_mythic_boss_pulls(report_data: JsonDict) -> dict[str, int]:
        from processor import canonical_boss_name

        counts: dict[str, int] = {}
        for fight in report_data.get("fights", []):
            if fight.get("difficulty") != mythic_difficulty or not fight.get("encounterID"):
                continue
            boss_name = canonical_boss_name(fight.get("name", "Unknown"))
            counts[boss_name] = counts.get(boss_name, 0) + 1
        return counts

    mode = config.get("mode", "guild")
    if args.guild or args.realm or args.region:
        mode = "guild"

    if mode == "guild" and config.get("guild", {}).get("enabled", True):
        logger.print("Guild mode selected. The program will fetch logs automatically; reports.txt is not required.")
        logger.print("Comparison mode: automatic guild-report discovery only. All tracked bosses are audited per report.")

        if args.guild or args.realm or args.region:
            if not args.guild or not args.realm:
                logger.print("If using command-line guild input, you must provide both --guild and --realm.")
                logger.print('Example: python START_HERE.py --guild "My Guild" --realm "Draenor" --region EU')
                return

            guild_profile = GuildProfile(
                name=args.guild.strip(),
                realm=args.realm.strip(),
                region=(args.region or config.get("guild", {}).get("region", "EU")).strip().upper(),
            )

            if args.save_guild:
                path = save_guild_profile_to_settings(guild_profile)
                logger.print(f"Saved guild profile to: {path}")
        else:
            guild_profile = resolve_guild_profile(config.get("guild", {}))

        season_config = config.get("season", {})
        start_ms, end_ms = season_range_ms(season_config)
        tz_name = season_config.get("timezone", "Europe/London")

        logger.print(f"Guild mode: {guild_profile.name}-{guild_profile.realm}-{guild_profile.region}")
        logger.print(f"Season: {season_config.get('name', 'Season')}")

        try:
            zones, zones_source = load_or_fetch_zones(client, force_refresh=force_refresh)
        except WCLApiError as e:
            logger.print(f"Failed to fetch Warcraft Logs zones: {e}")
            return

        logger.print(f"Zones source: {zones_source}")
        zone_lookup = build_zone_lookup(zones)

        zone_config = config.get("midnight_raid_zones", {})
        midnight_zone_ids = resolve_midnight_zone_ids(
            zones=zones,
            configured_ids=zone_config.get("zone_ids", []),
            name_contains=zone_config.get("name_contains", ["Midnight"]),
        )

        logger.print(f"Matched Midnight zone IDs: {sorted(midnight_zone_ids) if midnight_zone_ids else 'None from zone names/config'}")

        try:
            guild_reports, guild_reports_source = load_or_fetch_guild_reports(
                client=client,
                guild_name=guild_profile.name,
                realm=guild_profile.realm,
                region=guild_profile.region,
                start_ms=start_ms,
                end_ms=end_ms,
                force_refresh=force_refresh,
            )
        except WCLApiError as e:
            logger.print(f"Failed to fetch guild reports: {e}")
            return

        logger.print(f"Guild reports source: {guild_reports_source}")
        logger.print(f"Guild reports found in season range: {len(guild_reports)}")

        candidates = []
        boss_filter_config = config.get("boss_filter", {})
        selection_config = config.get("report_selection", {})
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
        if not force_refresh:
            for meta in guild_reports:
                code = report_code(meta)
                if not code:
                    continue
                try:
                    if cache.load_report(code) is not None:
                        cached_codes.add(code)
                except CacheError:
                    # A damaged cache entry should be re-fetched only if the
                    # metadata first pass selected it.
                    pass
        inspect_codes = deep_codes | cached_codes
        estimated_new_requests = len(deep_codes - cached_codes)
        logger.print(
            f"Metadata-first pass selected {len(deep_codes)} report(s); "
            f"{estimated_new_requests} new detailed WCL request(s) expected."
        )

        for meta in guild_reports:
            code = report_code(meta)
            if not code:
                continue

            local_date = report_date(meta, tz_name)
            meta_zone_match = report_matches_midnight_zone(
                report=meta,
                midnight_zone_ids=midnight_zone_ids,
                name_contains=zone_config.get("name_contains", ["Midnight"]),
                zone_lookup=zone_lookup,
            )

            if code not in inspect_codes:
                report_audit.append(
                    {
                        "date": local_date,
                        "report_code": code,
                        "title": meta.get("title", ""),
                        "start_ms": int(meta.get("start", 0)),
                        "end_ms": int(meta.get("end", 0)),
                        "zone_id": report_zone_id(meta),
                        "zone_name": report_zone_name(meta, zone_lookup),
                        "metadata_zone_match": meta_zone_match,
                        "cache_source": "not_requested",
                        "all_mythic_boss_pulls": 0,
                        "tracked_boss_pulls": 0,
                        "tracked_boss_breakdown": "",
                        "candidate": False,
                        "reason": metadata_reasons.get(code, "skipped_by_metadata_first_pass"),
                    }
                )
                continue

            try:
                report_data, source = cache.fetch_or_load_report(
                    report_code=code,
                    fetch_func=fetch_from_api,
                    force_refresh=force_refresh,
                    query={"difficulty": mythic_difficulty, "mode": "guild_auto_audit"},
                )

                all_boss_counts = count_all_mythic_boss_pulls(report_data)
                allowed_boss_counts = count_allowed_boss_pulls(report_data, boss_filter_config)
                allowed_total_pulls = sum(allowed_boss_counts.values())
                all_total_pulls = sum(all_boss_counts.values())

                boss_count_text = "; ".join(f"{boss}: {pulls}" for boss, pulls in sorted(allowed_boss_counts.items()))

                selected_reason = ""
                if allowed_total_pulls >= minimum_pulls:
                    candidates.append((meta, report_data, allowed_total_pulls, local_date))
                    selected_reason = "candidate_actual_allowed_boss_pulls"
                    logger.print(
                        f"Candidate {local_date}: {code} - {allowed_total_pulls} tracked boss pull(s) [{source}]"
                    )
                else:
                    selected_reason = "ignored_no_tracked_boss_pulls"

                report_audit.append(
                    {
                        "date": local_date,
                        "report_code": code,
                        "title": meta.get("title", report_data.get("title", "")),
                        "start_ms": int(meta.get("start", report_data.get("startTime", 0))),
                        "end_ms": int(meta.get("end", report_data.get("endTime", 0))),
                        "zone_id": report_zone_id(meta),
                        "zone_name": report_zone_name(meta, zone_lookup),
                        "metadata_zone_match": meta_zone_match,
                        "cache_source": source,
                        "all_mythic_boss_pulls": all_total_pulls,
                        "tracked_boss_pulls": allowed_total_pulls,
                        "tracked_boss_breakdown": boss_count_text,
                        "candidate": allowed_total_pulls >= minimum_pulls,
                        "reason": selected_reason,
                    }
                )

                for boss_name, pull_count in sorted(allowed_boss_counts.items()):
                    report_boss_audit.append(
                        {
                            "date": local_date,
                            "report_code": code,
                            "title": meta.get("title", report_data.get("title", "")),
                            "boss_name": boss_name,
                            "pulls": pull_count,
                            "candidate_report": allowed_total_pulls >= minimum_pulls,
                            "selected_final": False,
                            "final_reason": "",
                        }
                    )

            except (WCLApiError, CacheError) as e:
                logger.print(f"Failed to inspect report {code}: {e}")
                report_audit.append(
                    {
                        "date": local_date,
                        "report_code": code,
                        "title": meta.get("title", ""),
                        "start_ms": int(meta.get("start", 0)),
                        "end_ms": int(meta.get("end", 0)),
                        "zone_id": report_zone_id(meta),
                        "zone_name": report_zone_name(meta, zone_lookup),
                        "metadata_zone_match": meta_zone_match,
                        "cache_source": "failed",
                        "all_mythic_boss_pulls": 0,
                        "tracked_boss_pulls": 0,
                        "tracked_boss_breakdown": "",
                        "candidate": False,
                        "reason": f"failed_to_inspect: {e}",
                    }
                )

        chosen = choose_reports_by_day_or_session(
            report_candidates=candidates,
            tz_name=tz_name,
            zone_lookup=zone_lookup,
            selection_config=config.get("report_selection", {}),
        )

        chosen_codes = {item.code for item in chosen}
        reports = [
            report_data
            for meta, report_data, pulls, local_date in candidates
            if report_code(meta) in chosen_codes
        ]

        selected_reports = [
            {
                "date": item.date,
                "report_code": item.code,
                "title": item.title,
                "mythic_pull_count": item.mythic_pull_count,
                "zone_id": item.zone_id,
                "zone_name": item.zone_name,
            }
            for item in chosen
        ]

        selected_code_set = {item["report_code"] for item in selected_reports}
        final_reason_by_code = {}
        for audit_row in report_audit:
            if audit_row.get("candidate"):
                audit_row["selected_final"] = audit_row.get("report_code") in selected_code_set
                if audit_row["selected_final"]:
                    audit_row["final_reason"] = "selected_after_same_day_session_filter"
                else:
                    audit_row["final_reason"] = "candidate_but_removed_by_same_day_session_filter"
            else:
                audit_row["selected_final"] = False
                audit_row["final_reason"] = audit_row.get("reason", "")
            final_reason_by_code[audit_row.get("report_code")] = audit_row.get("final_reason", "")

        for boss_audit_row in report_boss_audit:
            code = boss_audit_row.get("report_code")
            boss_audit_row["selected_final"] = code in selected_code_set
            boss_audit_row["final_reason"] = final_reason_by_code.get(code, "")

        logger.print(f"Selected reports after same-day/session filter: {len(selected_reports)}")

    else:
        logger.print("Manual report mode selected.")
        try:
            report_codes = load_report_codes("reports.txt")
        except FileNotFoundError:
            logger.print("Could not find reports.txt.")
            logger.print('Guild mode does not need reports.txt. Check config.json has "mode": "guild".')
            return

        if not report_codes:
            logger.print("No report codes found in reports.txt.")
            logger.print('Guild mode does not need reports.txt. Check config.json has "mode": "guild".')
            return

        logger.print(f"Manual report mode. Found {len(report_codes)} report(s).")

        for idx, code in enumerate(report_codes, start=1):
            try:
                logger.print(f"[{idx}/{len(report_codes)}] Loading report {code}...")
                report, source = cache.fetch_or_load_report(
                    report_code=code,
                    fetch_func=fetch_from_api,
                    force_refresh=force_refresh,
                    query={"difficulty": mythic_difficulty},
                )
                logger.print(f"    Source: {source}")
                reports.append(report)
            except WCLApiError as e:
                logger.print(f"Failed to load {code}: {e}")
            except CacheError as e:
                logger.print(f"Cache error for {code}: {e}")

    if not reports:
        logger.print("No reports could be loaded or selected.")
        logger.print("Check guild/realm/region, API key, season range, and whether reports are public.")
        return

    logger.print("Classifying Mythic fights...")

    # Warn if the raw selected reports contain bosses outside the allow-list/exclude rules.
    boss_filter = config.get("boss_filter", {})
    if boss_filter.get("warn_on_unexpected_bosses", True):
        from processor import normalise_boss_name, canonical_boss_name
        allowed = {normalise_boss_name(x) for x in boss_filter.get("allowed_bosses", [])}
        excluded = {normalise_boss_name(x) for x in boss_filter.get("excluded_bosses", [])}
        seen_unexpected = set()
        for report in reports:
            for fight in report.get("fights", []):
                if fight.get("difficulty") != mythic_difficulty or not fight.get("encounterID"):
                    continue
                boss_name = canonical_boss_name(fight.get("name", "Unknown"))
                key = normalise_boss_name(boss_name)
                if key in excluded or (allowed and key not in allowed):
                    seen_unexpected.add(boss_name)
        for boss_name in sorted(seen_unexpected):
            warnings.append({"type": "boss_filtered_out", "message": f"Boss excluded by allow-list: {boss_name}", "boss_name": boss_name})
            logger.print(f"Warning: boss excluded by allow-list: {boss_name}")

    rows = classify_reports(
        reports,
        mythic_difficulty=mythic_difficulty,
        minimum_fight_seconds=minimum_fight_seconds,
        boss_filter=config.get("boss_filter", {}),
        time_config=config.get("time_calculation", {}),
    )

    logger.print(f"Classified {len(rows)} Mythic boss pull(s).")

    if not rows:
        logger.print("No Mythic boss fights found.")
        logger.print("Possible causes:")
        logger.print("- The reports are not Mythic raid logs.")
        logger.print("- The fights use a different difficulty ID than expected.")
        logger.print("- The API returned no boss fights.")
        logger.print("Try checking logs/latest_run.txt.")
        return

    export_outputs(
        rows_to_dicts(rows),
        output_folder=output_folder,
        selected_reports=selected_reports,
        warnings=warnings,
        report_audit=report_audit,
        report_boss_audit=report_boss_audit,
        clear_output_folder=bool(config.get("output", {}).get("clear_output_folder_before_export", True)),
    )

    logger.print("Done.")
    logger.print("A copy of this run log is saved to logs/latest_run.txt.")


if __name__ == "__main__":
    main()
