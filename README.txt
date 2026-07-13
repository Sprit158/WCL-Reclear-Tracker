WCL RECLEAR TRACKER v1.6.20.1 - PYTHON 3.14
========================================

This version starts using WCL v2 for guild report discovery.

IMPORTANT DISTINCTION
---------------------

There are two separate problems:

1. Finding guild names above you in rankings.
2. Fetching reports for a known guild.

v1.6.20.1 helps with problem 2.

It uses WCL v2 to fetch reports once we already know:

- guild name
- realm
- region

It does not magically solve rankings/guild-list discovery if WCL/third-party ranking pages do not expose a usable list.

WHAT v1.6.20.1 ADDS
----------------

- WCL v2 guild report discovery
- v1 vs v2 report audit
- comparison mode can use v2 report discovery with v1 fallback
- new .bat option:
  13. Test v2 guild reports
- new command:
  python START_HERE.py --test-v2-reports

OUTPUTS
-------

After using option 13:

output/v2_report_test.csv
output/v1_vs_v2_report_audit.csv

WHAT TO CHECK
-------------

Open:

output/v1_vs_v2_report_audit.csv

Look for rows where:

status = v2_only

Those are reports found by v2 that v1 did not return.

If the missing 13 May report appears as v2_only, then v2 solves the missing-report side and we can switch guild report discovery fully to v2.

COMPARISON MODE
---------------

config.json now has:

"report_discovery_mode": "v2_with_v1_fallback"

This means comparison mode will try v2 report discovery for each known guild, then fall back to v1 if v2 fails.

NEXT LIMITATION
---------------

Guild rankings/list discovery is still separate.

If automatic guild discovery still finds zero guilds, we need a proper rankings source/API endpoint. WCL v2 reportData can fetch reports for a known guild, but it is not necessarily a public guild-ranking search.


BUG FIX IN v1.6.20.1
-----------------

v1.5.4 option 13 crashed because it tried to use the v1 client before it had been created.

v1.6.20.1 moves the v2 report test until after the v1 fallback client is created.


BUG FIX IN v1.6.20.1
-----------------

v1.5.5 option 13 could crash while writing v1_vs_v2_report_audit.csv.

Cause:
  v1 report metadata can store zone as an integer, while v2 stores zone as an object/dictionary.

Fix:
  v1.6.20.1 now safely handles both shapes.


NEW IN v1.6.20.1
-------------

Guild discovery now has its own test mode.

BAT option:
  14. Test guild discovery only

Command:
  python START_HERE.py --test-discovery

This does not fetch WCL reports and does not spend WCL API points.

It writes:
  output/comparison/discovery_test.csv
  output/comparison/discovery_debug.txt

Discovery now tries multiple Raider.IO API parameter shapes and then an HTML fallback. This is to identify the exact working discovery route before processing lots of guild logs.


BUG FIX IN v1.6.20.1
-----------------

Guild discovery was failing because the Raider.IO raid slug was wrong.

Bad slug:
  midnight-season-1

Correct Raider.IO API raid slug:
  tier-mn-1

v1.6.20.1 updates config.json and the discovery defaults to use:

"raid_slug": "tier-mn-1"

Run:

14. Test guild discovery only

Then check:

output/comparison/discovery_test.csv
output/comparison/discovery_debug.txt


NEW IN v1.6.20.1 - DECLARED SCHEDULE SCAN
------------------------------------

v1.6.20.1 adds schedule scan mode.

BAT option:
  15. Schedule scan only

Command:
  python START_HERE.py --schedule-scan

This version deliberately does NOT infer schedule from logs yet.

It tries declared schedule sources only:

1. WCL guild page
2. WCL recruitment page/search page
3. Raider.IO guild page/profile text

Outputs:

output/comparison/schedule_scan.csv
output/comparison/schedule_scan_debug.txt
output/comparison/schedule_raw/

Important:
  This is a diagnostic build. The aim is to see whether declared schedule data can be extracted reliably before we use it as a filter.

Useful columns:
  declared_days_per_week
  declared_hours_per_week
  declared_raid_days
  declared_start_times
  schedule_source
  schedule_confidence
  matched_text
  wcl_guild_page_status
  wcl_recruitment_page_status
  raiderio_page_status


NEW IN v1.6.20 - WCL API SCHEDULE SCAN
-------------------------------------

Schedule scan now uses the Warcraft Logs v2 API directly.

Flow:
  1. Discover guilds from Raider.IO rankings.
  2. For each guild, query WCL v2 reportData.reports using guild/realm/region.
  3. Use report start/end times to build likely raid nights.
  4. Group nights by week.
  5. Calculate median nights/week and median hours/week.
  6. Mark likely 2-day guilds.

BAT option:
  15. Schedule scan only

Command:
  python START_HERE.py --schedule-scan

Outputs:
  output/comparison/schedule_scan.csv
  output/comparison/schedule_scan_debug.txt

Important:
  v1.6.20 does not inspect detailed combat events yet.
  It keeps zone filtering loose because report-level zone metadata can be wrong.
  This is intended as the efficient first pass before full reclear/prog processing.


NEW IN v1.6.20 - SQLITE SCHEDULE CACHE
-------------------------------------

Schedule scan now stores long-term data in:

%APPDATA%\WCLReclearTracker\database\comparison.sqlite

New/updated database tables:
  discovered_guilds
  schedule_report_cache
  schedule_scan_results
  schedule_raid_nights

This means cached schedule data can be searched later without re-fetching every guild.

BAT options:
  15. Schedule scan only
  16. Show cached likely 2-day guilds

Useful commands:
  python query_schedule_cache.py --two-day --limit 100
  python query_schedule_cache.py --days Mon Wed --min-hours 5 --max-hours 7.5
  python query_schedule_cache.py --min-hours 4.5 --max-hours 8

v1.6.20 still writes CSV/debug files:
  output/comparison/schedule_scan.csv
  output/comparison/schedule_scan_debug.txt

Legacy v1.6.1 JSON report-list caches will be migrated into SQLite when encountered.


NEW IN v1.6.20 - CHEAP CANDIDATE FILTER ONLY
-------------------------------------------

v1.6.20 deliberately does NOT fetch fight summaries during schedule scan.

Schedule scan is now Stage 1 only:
  - discover guilds
  - fetch WCL report lists
  - build likely raid nights from report dates/windows
  - calculate average raid days per active week
  - flag broad likely 2-day candidates
  - mark candidates as needing deep time review

It does not calculate Mythic fight-window hours at this stage.

Why:
  The deeper time calculation should only happen after a guild has been selected as a candidate. This avoids spending WCL calls on every guild.

Useful columns:
  average_raid_days_per_active_week
  inferred_days_per_week
  logged_window_hours_per_week
  is_likely_two_day
  candidate_needs_deep_time_review

The logged-window hours are only a broad first-pass value. They should not be used as final raid-time hours.


BUG FIX IN v1.6.20
-----------------

Fixed two bad import names in v1.6.4:

  comparison_database -> comparison_runner.database_path
  config_loader -> START_HERE.load_config

This fixes option 15 Schedule scan only and option 16 Show cached likely 2-day guilds.


BUG FIX IN v1.6.20
-----------------

Fixed option 16 query import:
  query_schedule_cache.py now imports load_config_early from main.py.


NEW IN v1.6.20 - RAIDER.IO DISCOVERY CACHE
-----------------------------------------

Raider.IO guild discovery is now cached in:

%APPDATA%\WCLReclearTracker\database\comparison.sqlite

Default discovery is now limited to the top 2000 EU guilds:

config.json:
  "max_rank_to_fetch": 2000
  "max_pages": 10
  "api_limit": 200
  "cache_enabled": true
  "cache_ttl_hours": 168
  "force_refresh_cache": false

Expected behaviour:
  - First run fetches roughly 10 Raider.IO pages and caches the result.
  - Later runs reuse the cached discovery list.
  - Set force_refresh_cache=true if you want to refresh rankings.


NEW IN v1.6.20 - FASTER REPEATED SCHEDULE SCANS
-----------------------------------------------

Option 15 now skips guilds that already have a row in schedule_scan_results.

This means repeated runs of:

  15. Schedule scan only

will process the next unscanned batch instead of re-processing the same guilds.

New config options:

  "skip_existing_schedule_results": true
  "retry_error_results": false
  "scan_only_unprocessed_guilds": true

Existing cache behaviour remains:

  - Raider.IO discovery cache is reused.
  - WCL report-list cache is reused.
  - Schedule results are stored in comparison.sqlite.

To rescan everything, set:

  "skip_existing_schedule_results": false

or delete/clear the database.


NEW IN v1.6.20 - PARALLEL SCHEDULE SCAN
--------------------------------------

Option 15 can now fetch WCL report lists in parallel using threads.

Default config:

  "parallel_schedule_scan": true
  "schedule_scan_workers": 4
  "parallel_fetch_only": true
  "main_thread_sqlite_writes": true
  "max_retry_attempts": 2
  "wcl_request_delay_seconds": 0.1

How it works:
  - Main thread checks SQLite cache first.
  - Cached guilds are processed immediately.
  - Uncached guilds are fetched concurrently.
  - Worker threads do HTTP fetches only.
  - SQLite cache/result writes stay on the main thread.

This should speed up batches where many guilds are not cached yet, while avoiding SQLite write locking.


NEW IN v1.6.20 - PROGRESSION PERIOD SCHEDULE FILTER
---------------------------------------------------

Schedule scan can now filter report-list schedule data to the guild's own progression period.

Why:
  Some guilds raid 3 days during progression, then drop to 2 days after killing Lura / Midnight Falls.
  Using the whole season can mislabel those guilds as 2-day guilds.

What changed:
  - Raider.IO discovery now tries to capture the endboss kill timestamp/date.
  - Schedule scan uses reports up to that kill date when available.
  - Reports after the kill date are excluded from first-pass schedule classification.
  - Output includes:
      progression_cutoff_date
      progression_cutoff_source
      reports_after_cutoff_excluded

Config:
  "schedule_period_mode": "progression_until_endboss_kill"
  "use_guild_endboss_kill_cutoff": true
  "endboss_name": "Midnight Falls"
  "include_kill_day_in_schedule": true
  "fallback_when_kill_date_missing": "use_full_season"

Note:
  If Raider.IO does not expose the kill timestamp in the ranking payload for a guild,
  the scan falls back to the full season for that guild.


BUG FIX IN v1.6.20
------------------

Fixed old SQLite database migration.

v1.6.10 added endboss kill-date columns, but existing comparison.sqlite
databases did not automatically get those new columns. v1.6.20 now runs
explicit ALTER TABLE migrations before writing discovery results.

This fixes:
  sqlite3.OperationalError: table discovered_guilds has no column named endboss_kill_timestamp_ms


NEW IN v1.6.20 - WCL ENDBOSS KILL FALLBACK
------------------------------------------

Raider.IO ranking discovery does not always expose a usable Lura / Midnight Falls kill timestamp.

v1.6.20 now falls back to WCL fight summaries only to find the first Mythic endboss kill date.

This is not a full deep time dive:
  - it only checks report fight summaries
  - it only looks for the first Mythic Midnight Falls / Lura kill
  - the result is cached in SQLite

New cache table:
  endboss_kill_cache

Config:
  "fallback_find_endboss_kill_from_wcl": true
  "endboss_kill_cache_enabled": true
  "endboss_kill_cache_ttl_hours": 720
  "endboss_encounter_names": ["Midnight Falls", "Lura"]

Once the kill date is found, schedule scan excludes reports after that date.


BUG FIX IN v1.6.20 - CLEAR CACHE NOW CLEARS SQLITE
---------------------------------------------------

The old Clear Warcraft Logs cache option only deleted:

  %APPDATA%\WCLReclearTracker\cache

Most newer data is now stored in:

  %APPDATA%\WCLReclearTracker\database\comparison.sqlite

v1.6.20 updates option 8 / --clear-cache so it clears both:
  - file cache
  - SQLite cache tables

SQLite tables cleared:
  raiderio_discovery_cache
  schedule_report_cache
  schedule_scan_results
  schedule_raid_nights
  endboss_kill_cache


BUG FIX IN v1.6.20
------------------

Fixed option 8 import error:

  NameError: name 'clear_all_global_caches' is not defined

main.py now imports clear_all_global_caches from settings_manager.py.


BUG FIX IN v1.6.20 - IGNORE BOOLEAN DEFEATED FIELDS
----------------------------------------------------

Fixed the issue where Raider.IO field:

  isDefeated = true

was interpreted as timestamp 1, causing:

  progression_cutoff_date = 1970-01-01

v1.6.20 ignores boolean defeated/cleared fields and only accepts real date/time fields.
The discovery cache schema version has been bumped so the bad cached 1970 data is not reused.


NEW IN v1.6.20 - WOWPROGRESS TEST MODE
--------------------------------------

Added a test-only WoWProgress scraper.

Menu option:
  17. Test WoWProgress 1-2 raids/week

Command:
  python START_HERE.py --test-wowprogress

It fetches the WoWProgress filtered page:
  raids_week=1-2

and writes:
  output/comparison/wowprogress_1_2_raids.csv
  output/comparison/wowprogress_debug.txt

This is not yet wired into the main comparison. It is only to confirm whether
we can reliably parse guild, realm, region, rank and progress from WoWProgress.


NEW IN v1.6.20 - WOWPROGRESS BACKUP FALLBACK
--------------------------------------------

The schedule scan now uses the manually-created WoWProgress 1-2 raids/week
backup sheet when WCL has no usable data.

Logic:
  1. Try WCL first.
  2. If WCL returns reports, use WCL-derived schedule data.
  3. If WCL returns 0 public reports, check data/wowprogress_1_2_day_backup.csv.
  4. If WCL guild lookup/API fails, check data/wowprogress_1_2_day_backup.csv.
  5. If matched, mark the guild as declared 1-2 raids/week from WoWProgress backup.

Important:
  WoWProgress backup matches are declared-only. They are not measured raid hours.

New output values:
  schedule_source = wowprogress_screenshot_backup_declared_only
  schedule_confidence = declared_only
  inferred_raid_days = declared 1-2 raids/week


NEW IN v1.6.20 - SCAN EVERY DECLARED 1-2 DAY GUILD ABOVE YOU
-------------------------------------------------------------

Option 15 now uses the local WoWProgress backup list as its schedule-scan source
by default.

Instead of scanning a 50-guild Raider.IO window, it selects every guild from:

  data/wowprogress_1_2_day_backup.csv

where:
  - declared raids/week = 1-2
  - world rank is above the configured guild

It still works in batches:
  max_guilds_per_run = 25

So each run of option 15 scans the next 25 unprocessed declared 1-2 day
guilds above the configured guild.

WCL is still tried first. If WCL has no public data, the row is marked as:
  schedule_source = wowprogress_screenshot_backup_declared_only


NEW IN v1.6.20 - ZERO-WCL DECLARED-ONLY OPTION 15
-------------------------------------------------

Option 15 now defaults to a declared-only scan from the local WoWProgress backup.

This means:
  - it scans every declared 1-2 day guild above the configured guild
  - it does not query WCL
  - it uses 0 WCL API points/tokens

This is deliberate because WCL enrichment can be expensive. After a shortlist is
built, WCL enrichment should be run only for selected guilds.

Config:
  comparison.wowprogress_backup.declared_only_scan = true
  comparison.wowprogress_backup.wcl_enrichment_enabled = false

To re-enable WCL checks inside option 15, set:
  "wcl_enrichment_enabled": true

but this will use WCL API points.


NEW IN v1.6.20 - CHANGEABLE REGION FILTER FOR BACKUP SCAN
----------------------------------------------------------

Option 15 now filters the WoWProgress backup list by region.

Default:
  "region_filter": "EU"

Config location:
  comparison.wowprogress_backup.region_filter

Allowed values:
  "EU"  -> EU only
  "US"  -> US only
  "OC"  -> Oceanic only
  ""    -> world/all regions

This only affects the WoWProgress backup source used by option 15.
