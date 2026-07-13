# WCL Reclear Tracker

A local Python tool for comparing World of Warcraft Mythic guild progression, reclear time and likely raid schedules using Warcraft Logs and ranking data.

## Windows quick start

1. Download the latest `v*.zip` from [Releases](https://github.com/Sprit158/WCL-Reclear-Tracker/releases/latest).
2. Extract the ZIP.
3. Double-click `START_WCL_RECLEAR_TRACKER.bat`.

The main launcher automatically asks for and saves missing guild and Warcraft Logs details. Maintenance and credential-reset tools are grouped under **Settings and maintenance**.

Choose **5. Check for and install updates** from the launcher menu. Updates are downloaded from this repository and applied in place while preserving local guild data and settings.

Version 1.7.2 uses a metadata-first report pass. It filters known unrelated zones and duplicate report sessions before downloading detailed fight summaries, while continuing to reuse all cached report details at zero additional API-point cost.

The local WoWProgress backup is copied to the persistent app-data folder the first time it is found. If a newly unpacked version is missing it, the app can recover the CSV from an older adjacent version automatically.

Version 1.7.3 processes every eligible guild above the saved guild rank instead of stopping after 25. A one-guild WCL v2 test verifies raid nights from target-zone report metadata and displays the WCL points used before wider enrichment is enabled.

Version 1.7.4 enables that metadata verification for the full eligible guild list. Raw report lists, calculated schedule summaries and individual raid nights are stored in SQLite; fresh report lists are reused for seven days, and each batch displays its measured WCL point cost.

Version 1.7.5 renders saved likely two-day guilds as a compact console table. It also stores `first_month_average_raid_days`, calculated across the first four reset weeks from the configured season start with zero-night weeks included.

Version 1.7.6 uses adaptive weekday recurrence to separate stable core raid days from progression overtime. It recognises one-, two- and three-day schedules, lowers confidence when several weekdays are tied by a rotating or changed schedule, and displays recurring core days plus overtime per active week. Any-length report containing a Mythic boss fight counts as a raid day; fight summaries are fetched only for otherwise-too-short reports and are then cached permanently. The saved guild is included in the comparison table and marked `(you)`.

Python 3.10 or newer is required. The launcher recognises `py -3`, `python` and `python3`.

## Privacy

Warcraft Logs credentials, cached API responses and the comparison database are stored locally under `%APPDATA%\WCLReclearTracker`. They are not included in this repository or release ZIPs.

The optional WoWProgress guild-ranking backup is also local-only and is not published in the repository or release ZIPs.

## Releases

Changing `version.txt` on the `main` branch runs the self-check and automatically creates a GitHub release with a version-number-only ZIP filename.
