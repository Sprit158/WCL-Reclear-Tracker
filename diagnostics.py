from __future__ import annotations

from pathlib import Path
import importlib.util
import os
import sys

from processor import load_report_codes
from settings_manager import get_api_key_from_settings, get_global_settings_path, get_global_cache_dir, get_guild_profile_from_settings, mask_key


REQUIRED_MODULES = ["requests", "pandas", "openpyxl", "dotenv"]


def module_ok(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def run_check_settings(config: dict) -> None:
    print("WCL Reclear Tracker - Settings Check")
    print("------------------------------------")
    print(f"Python version: {sys.version.split()[0]}")
    print(f"Python target: {config.get('target_python', 'Not set')}")
    print()

    print("Required packages:")
    for module in REQUIRED_MODULES:
        print(f"  {module}: {'OK' if module_ok(module) else 'Missing'}")
    print()

    settings_path = get_global_settings_path()
    saved_key = get_api_key_from_settings()
    env_key = os.getenv("WCL_API_KEY")

    print("Warcraft Logs key:")
    print(f"  Environment/.env: {mask_key(env_key)}")
    print(f"  Global settings: {mask_key(saved_key.api_key if saved_key else None)}")
    print(f"  Settings file: {settings_path}")
    print()

    cache_config = config.get("cache", {})
    if cache_config.get("location") == "global_app_data":
        cache_folder = get_global_cache_dir(cache_config.get("folder", "reports"))
    else:
        cache_folder = Path(cache_config.get("folder", "cache/reports"))

    print("Cache:")
    print(f"  Enabled: {cache_config.get('enabled', True)}")
    print(f"  Location: {cache_config.get('location', 'local_program_folder')}")
    print(f"  Folder: {cache_folder}")
    print(f"  Folder exists: {'Yes' if cache_folder.exists() else 'No'}")
    print(f"  Schema: {cache_config.get('schema_version', 1)}")
    print(f"  Supported schemas: {cache_config.get('supported_schema_versions', [1])}")
    print()

    saved_guild = get_guild_profile_from_settings()
    print("Guild:")
    if saved_guild:
        print(f"  Saved guild: {saved_guild.name}-{saved_guild.realm}-{saved_guild.region}")
    else:
        print("  Saved guild: Not found")
    print()

    try:
        report_codes = load_report_codes("reports.txt")
        print("Manual reports fallback:")
        print(f"  reports.txt found: Yes")
        print(f"  Report codes found: {len(report_codes)}")
    except FileNotFoundError:
        print("Manual reports fallback:")
        print(f"  reports.txt found: No")
        print(f"  Report codes found: 0")
    print()
