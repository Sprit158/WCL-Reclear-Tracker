# WCL Reclear Tracker

A local Python tool for comparing World of Warcraft Mythic guild progression, reclear time and likely raid schedules using Warcraft Logs and ranking data.

## Windows quick start

1. Download the latest `v*.zip` from [Releases](https://github.com/Sprit158/WCL-Reclear-Tracker/releases/latest).
2. Extract the ZIP.
3. Double-click `START_WCL_RECLEAR_TRACKER.bat`.

After installation, choose **18. Check for and install updates** from the launcher menu. Updates are downloaded from this repository and applied in place while preserving local guild data and settings.

Python 3.10 or newer is required. The launcher recognises `py -3`, `python` and `python3`.

## Privacy

Warcraft Logs credentials, cached API responses and the comparison database are stored locally under `%APPDATA%\WCLReclearTracker`. They are not included in this repository or release ZIPs.

The optional WoWProgress guild-ranking backup is also local-only and is not published in the repository or release ZIPs.

## Releases

Changing `version.txt` on the `main` branch runs the self-check and automatically creates a GitHub release with a version-number-only ZIP filename.
