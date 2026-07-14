from __future__ import annotations

from pathlib import Path


def main() -> None:
    main_text = Path("main.py").read_text(encoding="utf-8")
    config_text = Path("config.json").read_text(encoding="utf-8")

    checks = [
        ("No early report_codes log before guild mode", "logger.print(f\"Found {len(report_codes)} report(s).\")" not in main_text),
        ("GuildProfile imported", "GuildProfile" in main_text and "from settings_manager import (" in main_text),
        ("Global cache resolver present", "resolve_cache_folder" in main_text),
        ("Guild mode reports.txt not required message", "reports.txt is not required" in main_text),
        ("Boss allow-list configured", '"boss_filter"' in config_text and '"Rotmire"' in config_text),
        ("Same-day report combiner used", "choose_reports_by_day_or_session" in main_text),
        ("Window time exported", "window_segment_hours" in Path("exporter.py").read_text(encoding="utf-8")),
        ("Pulls by phase exported", "pulls_by_phase.csv" in Path("exporter.py").read_text(encoding="utf-8")),
        ("Efficiency summary exported", "efficiency_summary.csv" in Path("exporter.py").read_text(encoding="utf-8")),
        ("Reclear tax exported", "reclear_tax_percent" in Path("exporter.py").read_text(encoding="utf-8")),
        ("Boss wall percent exported", "boss_wall_percent" in Path("exporter.py").read_text(encoding="utf-8")),
        ("Clear cache command present", "--clear-cache" in Path("main.py").read_text(encoding="utf-8")),
        ("Clear output command present", "--clear-output" in Path("main.py").read_text(encoding="utf-8")),
        ("Output clearing export present", "clear_output_folder" in Path("exporter.py").read_text(encoding="utf-8")),
        ("Report audit exported", "report_audit.csv" in Path("exporter.py").read_text(encoding="utf-8")),
        ("Reports selected by actual boss pulls", "tracked_boss_pulls" in Path("main.py").read_text(encoding="utf-8")),
        ("No manual report-code workaround", not Path("extra_reports.txt").exists() and "load_extra_report" not in Path("main.py").read_text(encoding="utf-8")),
        ("All-boss report audit exported", "report_boss_audit.csv" in Path("exporter.py").read_text(encoding="utf-8")),
        ("Comparison mode present", "--comparison" in Path("main.py").read_text(encoding="utf-8")),
        ("SQLite comparison database module present", Path("comparison_db.py").exists()),
        ("comparison_guilds.csv present", Path("comparison_guilds.csv").exists()),
        ("Guild discovery module present", Path("guild_discovery.py").exists()),
        ("Discovery output configured", "discovered_guilds.csv" in Path("config.json").read_text(encoding="utf-8")),
        ("RaiderIO discovery configured", "raiderio_raid_rankings_api" in Path("config.json").read_text(encoding="utf-8")),
        ("WCL v2 setup present", "--setup-v2" in Path("main.py").read_text(encoding="utf-8")),
        ("WCL v2 API client present", Path("api/wcl_api_v2.py").exists()),
        ("v2 report discovery test present", "--test-v2-reports" in Path("main.py").read_text(encoding="utf-8")),
        ("v1/v2 audit safe zone handling", "safe_zone_name" in Path("v2_report_tools.py").read_text(encoding="utf-8")),
        ("Discovery-only test present", "--test-discovery" in Path("main.py").read_text(encoding="utf-8")),
        ("WoWProgress test mode", Path("wowprogress_test.py").exists() and "--test-wowprogress" in Path("main.py").read_text(encoding="utf-8")),
        ("WoWProgress local-only backup support", Path("wowprogress_backup.py").exists() and "wowprogress_screenshot_backup_declared_only" in Path("schedule_scan.py").read_text(encoding="utf-8")),
        ("WoWProgress above-own scan source", "rows_from_wowprogress_backup_for_schedule_scan" in Path("wowprogress_backup.py").read_text(encoding="utf-8") and "Using WoWProgress backup as schedule-scan source" in Path("schedule_scan.py").read_text(encoding="utf-8")),
        ("Declared-only zero-WCL scan", "declared_only_backup_schedule_result" in Path("schedule_scan.py").read_text(encoding="utf-8") and "\"WCL API fetches this run: 0\"" in Path("schedule_scan.py").read_text(encoding="utf-8")),
        ("Backup region filter configurable", "\"region_filter\": \"EU\"" in Path("config.json").read_text(encoding="utf-8") and "raw_region_filter" in Path("wowprogress_backup.py").read_text(encoding="utf-8")),
        ("Discovery test module present", Path("discovery_test.py").exists()),
        ("Correct Raider.IO raid slug", '"raid_slug": "tier-mn-1"' in Path("config.json").read_text(encoding="utf-8")),
        ("Schedule scan present", "--schedule-scan" in Path("main.py").read_text(encoding="utf-8")),
        ("Schedule scan module present", Path("schedule_scan.py").exists()),
        ("WCL API schedule scan", "wcl_v2 guild report lists" in Path("schedule_scan.py").read_text(encoding="utf-8") or "fetch_guild_reports_v2_cached" in Path("schedule_scan.py").read_text(encoding="utf-8")),
        ("SQLite schedule database", Path("schedule_database.py").exists()),
        ("SQLite cache clear", "clear_global_database_cache" in Path("settings_manager.py").read_text(encoding="utf-8") and "clear_all_global_caches" in Path("main.py").read_text(encoding="utf-8")),
        ("Clear all global caches imported", "clear_all_global_caches," in Path("main.py").read_text(encoding="utf-8") and "def clear_all_global_caches" in Path("settings_manager.py").read_text(encoding="utf-8")),
        ("Schedule query script", Path("query_schedule_cache.py").exists()),
        ("Progression kill cutoff", "progression_cutoff_date" in Path("schedule_scan.py").read_text(encoding="utf-8") and "endboss_kill_timestamp_ms" in Path("guild_discovery.py").read_text(encoding="utf-8")),
        ("Boolean defeated ignored", "isinstance(value, bool)" in Path("guild_discovery.py").read_text(encoding="utf-8") and "blocked_timestamp_keys" in Path("guild_discovery.py").read_text(encoding="utf-8")),
        ("SQLite kill column migration", "run_schema_migrations" in Path("schedule_database.py").read_text(encoding="utf-8") and "endboss_kill_timestamp_ms" in Path("schedule_database.py").read_text(encoding="utf-8")),
        ("WCL endboss kill fallback", "find_endboss_kill_from_wcl_reports" in Path("schedule_scan.py").read_text(encoding="utf-8") and "endboss_kill_cache" in Path("schedule_database.py").read_text(encoding="utf-8")),
        ("Cheap candidate filter mode", "candidate_needs_deep_time_review" in Path("schedule_scan.py").read_text(encoding="utf-8")),
        ("v1.6.20 query import", "from main import load_config_early" in Path("query_schedule_cache.py").read_text(encoding="utf-8")),
        ("Option 13 client-order fix", Path("main.py").read_text(encoding="utf-8").find("if args.test_v2_reports") > Path("main.py").read_text(encoding="utf-8").find("client = WCLClient")),
        ("GitHub updater present", Path("updater.py").exists() and "releases/latest" in Path("updater.py").read_text(encoding="utf-8")),
        ("Launcher update option", "Check for and install updates" in Path("START_WCL_RECLEAR_TRACKER.bat").read_text(encoding="utf-8")),
        ("Updater preserves local guild data", "data/wowprogress_1_2_day_backup.csv" in Path("updater.py").read_text(encoding="utf-8") and "comparison_guilds.csv" in Path("updater.py").read_text(encoding="utf-8")),
        ("Metadata-first WCL request reduction", "shortlist_reports_for_deep_inspection" in Path("guild_fetcher.py").read_text(encoding="utf-8") and '"metadata_first"' in config_text),
        ("WoWProgress backup persists between versions", "global_backup_path" in Path("wowprogress_backup.py").read_text(encoding="utf-8") and "destination.exists()" in Path("updater.py").read_text(encoding="utf-8")),
        ("Schedule scan uses saved own guild", "get_guild_profile_from_settings" in Path("wowprogress_backup.py").read_text(encoding="utf-8") and "Own guild used" in Path("schedule_scan.py").read_text(encoding="utf-8")),
        ("Schedule scan supports all eligible guilds", '"max_guilds_per_run": 0' in config_text and "process_all = verification_enabled or declared_only_mode or max_guilds <= 0" in Path("schedule_scan.py").read_text(encoding="utf-8") and "rebuilds the complete eligible guild list" in Path("schedule_scan.py").read_text(encoding="utf-8")),
        ("One-guild schedule point-cost test", "--test-schedule-guild" in main_text and "run_single_guild_schedule_test" in Path("schedule_scan.py").read_text(encoding="utf-8") and "Points used by test" in Path("schedule_scan.py").read_text(encoding="utf-8")),
        ("WCL point test keeps decimal precision", "float(before.get" in Path("schedule_scan.py").read_text(encoding="utf-8") and "round(end - start, 2)" in Path("schedule_scan.py").read_text(encoding="utf-8")),
        ("Actual schedules cached in SQLite", "get_latest_cached_reports" in Path("schedule_database.py").read_text(encoding="utf-8") and '"actual_schedule_verification_enabled": true' in config_text and "sqlite_cache_latest" in Path("schedule_scan.py").read_text(encoding="utf-8")),
        ("Batch schedule zone filter", "filter_target_raid_reports(reports, config)" in Path("schedule_scan.py").read_text(encoding="utf-8")),
        ("Batch WCL point meter", "WCL points used this run" in Path("schedule_scan.py").read_text(encoding="utf-8")),
        ("One-guild test populates shared cache", "Reuse everything collected by this test" in Path("schedule_scan.py").read_text(encoding="utf-8") and "wcl_v2_single_guild_report_list_test" in Path("schedule_scan.py").read_text(encoding="utf-8")),
        ("Missing WCL data stays unverified", "wowprogress_backup_unverified_no_public_reports" in Path("schedule_scan.py").read_text(encoding="utf-8") and "wowprogress_backup_unverified_wcl_error" in Path("schedule_scan.py").read_text(encoding="utf-8")),
        ("Seven-day report-list cache", '"report_list_cache_ttl_hours": 168' in config_text),
        ("Readable saved-guild table", "def render_table" in Path("query_schedule_cache.py").read_text(encoding="utf-8") and "M1/wk" in Path("query_schedule_cache.py").read_text(encoding="utf-8")),
        ("First-month raid-day average", Path("schedule_scan.py").read_text(encoding="utf-8").count("first_month_average_raid_days") >= 4 and "first_month_average_raid_days" in Path("schedule_database.py").read_text(encoding="utf-8")),
        ("Adaptive core-days plus overtime algorithm", "infer_core_raid_days" in Path("schedule_scan.py").read_text(encoding="utf-8") and "core_day_ambiguity_gap" in Path("schedule_scan.py").read_text(encoding="utf-8") and '"minimum_counted_raid_day_minutes": 15' in config_text),
        ("Short Mythic reports count and cache", "hydrate_short_report_mythic_evidence" in Path("schedule_scan.py").read_text(encoding="utf-8") and "schedule_report_fight_cache" in Path("schedule_database.py").read_text(encoding="utf-8")),
        ("Own guild included in comparison table", '"include_own": true' in config_text and "saved_own_guild_reference" in Path("wowprogress_backup.py").read_text(encoding="utf-8") and "own_rank is not None and not own_already_present" not in Path("wowprogress_backup.py").read_text(encoding="utf-8") and "Process the user's reference row first" in Path("wowprogress_backup.py").read_text(encoding="utf-8") and "(you)" in Path("query_schedule_cache.py").read_text(encoding="utf-8") and "Your saved guild is missing from the schedule cache" in Path("query_schedule_cache.py").read_text(encoding="utf-8")),
        ("Own guild forced into real scan queue", "ensure_saved_own_guild_row" in Path("schedule_scan.py").read_text(encoding="utf-8") and "saved_own_guild_forced_scan" in Path("schedule_scan.py").read_text(encoding="utf-8")),
        ("All ranked guilds scanned through own rank", '"scan_all_ranked_guilds_to_own": true' in config_text and "raiderio_all_ranked_to_own" in Path("schedule_scan.py").read_text(encoding="utf-8") and "int(item.rank) <= int(cutoff_rank)" in Path("schedule_scan.py").read_text(encoding="utf-8")),
        ("All checked schedules shown", "Show all checked guild schedules" in Path("START_WCL_RECLEAR_TRACKER.bat").read_text(encoding="utf-8") and "query_schedule_cache.py --limit 1000" in Path("START_WCL_RECLEAR_TRACKER.bat").read_text(encoding="utf-8") and '("2d?", "is_likely_two_day"' in Path("query_schedule_cache.py").read_text(encoding="utf-8")),
    ]

    failed = [name for name, ok in checks if not ok]

    print("Self-check results:")
    for name, ok in checks:
        print(f"  {'OK' if ok else 'FAIL'} - {name}")

    if failed:
        raise SystemExit(1)

    print("Self-check passed.")


if __name__ == "__main__":
    main()
