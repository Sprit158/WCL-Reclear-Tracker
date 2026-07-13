from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import json
import os
import sys
import shutil


JsonDict = dict[str, Any]


APP_FOLDER_NAME = "WCLReclearTracker"
SETTINGS_FILE_NAME = "settings.json"


@dataclass(slots=True)
class WCLApiKey:
    api_key: str


@dataclass(slots=True)
class WCLV2Credentials:
    client_id: str
    client_secret: str


@dataclass(slots=True)
class WCLV2Token:
    access_token: str
    expires_at_utc: int
    token_type: str = "Bearer"


class SettingsError(Exception):
    pass


def get_global_settings_dir() -> Path:
    if sys.platform.startswith("win"):
        appdata = os.getenv("APPDATA")
        if appdata:
            return Path(appdata) / APP_FOLDER_NAME

    return Path.home() / ".wcl_reclear_tracker"


def get_global_settings_path() -> Path:
    return get_global_settings_dir() / SETTINGS_FILE_NAME


def load_global_settings() -> JsonDict:
    path = get_global_settings_path()

    if not path.exists():
        return {}

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SettingsError(f"Settings file is not valid JSON: {path} ({e})") from e


def save_global_settings(settings: JsonDict) -> Path:
    folder = get_global_settings_dir()
    folder.mkdir(parents=True, exist_ok=True)

    path = get_global_settings_path()
    path.write_text(
        json.dumps(settings, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return path


def get_api_key_from_settings() -> WCLApiKey | None:
    settings = load_global_settings()
    wcl = settings.get("warcraft_logs", {})

    api_key = wcl.get("api_key_v1") or wcl.get("api_key")

    if api_key:
        return WCLApiKey(api_key=api_key)

    return None


def save_api_key_to_settings(api_key: WCLApiKey) -> Path:
    settings = load_global_settings()

    settings.setdefault("schema_version", 1)
    settings.setdefault("warcraft_logs", {})
    settings["warcraft_logs"]["api_mode"] = "v1_api_key"
    settings["warcraft_logs"]["api_key_v1"] = api_key.api_key

    return save_global_settings(settings)


def reset_saved_api_key() -> bool:
    settings = load_global_settings()
    wcl = settings.get("warcraft_logs", {})

    removed = False
    for key_name in ["api_key_v1", "api_key"]:
        if key_name in wcl:
            del wcl[key_name]
            removed = True

    if removed:
        settings["warcraft_logs"] = wcl
        save_global_settings(settings)

    return removed


def prompt_for_api_key() -> WCLApiKey:
    print()
    print("No Warcraft Logs v1 API key found.")
    print("Paste your Warcraft Logs v1 API key below.")
    print("It will be saved globally for future versions of this tracker.")
    print()
    print("Note: the key will be visible while typing. This avoids issues when running from the .bat file.")
    print()

    api_key = input("Warcraft Logs v1 API Key: ").strip()

    if not api_key:
        raise SettingsError("Warcraft Logs API key cannot be blank.")

    return WCLApiKey(api_key=api_key)


def resolve_wcl_api_key(
    env_api_key: str | None,
    use_global_settings: bool = True,
) -> WCLApiKey:
    if env_api_key:
        return WCLApiKey(api_key=env_api_key)

    if use_global_settings:
        saved = get_api_key_from_settings()
        if saved:
            return saved

        entered = prompt_for_api_key()
        path = save_api_key_to_settings(entered)
        print(f"Saved Warcraft Logs API key to: {path}")
        return entered

    raise SettingsError("No WCL_API_KEY found. Add WCL_API_KEY to .env.")


def mask_key(api_key: str | None) -> str:
    if not api_key:
        return "Not found"

    if len(api_key) <= 8:
        return "*" * len(api_key)

    return api_key[:4] + "..." + api_key[-4:]


def get_global_cache_dir(cache_subfolder: str = "reports") -> Path:
    """
    Returns the global shared cache folder.

    Windows:
        %APPDATA%/WCLReclearTracker/cache/reports

    Other:
        ~/.wcl_reclear_tracker/cache/reports
    """

    return get_global_settings_dir() / "cache" / cache_subfolder


@dataclass(slots=True)
class GuildProfile:
    name: str
    realm: str
    region: str


def get_guild_profile_from_settings() -> GuildProfile | None:
    settings = load_global_settings()
    guild = settings.get("guild", {})

    name = guild.get("name")
    realm = guild.get("realm")
    region = guild.get("region")

    if name and realm and region:
        return GuildProfile(name=name, realm=realm, region=region)

    return None


def save_guild_profile_to_settings(profile: GuildProfile) -> Path:
    settings = load_global_settings()

    settings.setdefault("schema_version", 1)
    settings["guild"] = {
        "name": profile.name,
        "realm": profile.realm,
        "region": profile.region,
    }

    return save_global_settings(settings)


def prompt_for_guild_profile(default_region: str = "EU") -> GuildProfile:
    print()
    print("No guild profile found.")
    print("Enter the guild details once. They will be saved globally for future versions.")
    print()

    name = input("Guild name: ").strip()
    realm = input("Realm name: ").strip()
    region = input(f"Region [{default_region}]: ").strip() or default_region

    if not name or not realm or not region:
        raise SettingsError("Guild name, realm, and region cannot be blank.")

    return GuildProfile(name=name, realm=realm, region=region.upper())


def resolve_guild_profile(config_guild: JsonDict) -> GuildProfile:
    cfg_name = (config_guild.get("name") or "").strip()
    cfg_realm = (config_guild.get("realm") or "").strip()
    cfg_region = (config_guild.get("region") or "EU").strip().upper()
    save_to_settings = bool(config_guild.get("save_to_global_settings", True))

    if cfg_name and cfg_realm and cfg_region:
        profile = GuildProfile(name=cfg_name, realm=cfg_realm, region=cfg_region)
        if save_to_settings:
            save_guild_profile_to_settings(profile)
        return profile

    saved = get_guild_profile_from_settings()
    if saved:
        return saved

    entered = prompt_for_guild_profile(default_region=cfg_region)
    if save_to_settings:
        path = save_guild_profile_to_settings(entered)
        print(f"Saved guild profile to: {path}")

    return entered


def reset_saved_guild_profile() -> bool:
    settings = load_global_settings()

    if "guild" not in settings:
        return False

    del settings["guild"]
    save_global_settings(settings)
    return True



def get_global_cache_root() -> Path:
    return get_global_settings_dir() / "cache"


def clear_global_cache() -> tuple[bool, Path]:
    cache_root = get_global_cache_root()

    if not cache_root.exists():
        return False, cache_root

    shutil.rmtree(cache_root)
    return True, cache_root



def get_v2_credentials_from_settings() -> WCLV2Credentials | None:
    settings = load_global_settings()
    wcl = settings.get("warcraft_logs", {})
    v2 = wcl.get("v2_oauth", {})

    client_id = v2.get("client_id") or wcl.get("client_id")
    client_secret = v2.get("client_secret") or wcl.get("client_secret")

    if client_id and client_secret:
        return WCLV2Credentials(client_id=client_id, client_secret=client_secret)

    return None


def save_v2_credentials_to_settings(credentials: WCLV2Credentials) -> Path:
    settings = load_global_settings()

    settings.setdefault("schema_version", 1)
    settings.setdefault("warcraft_logs", {})
    settings["warcraft_logs"]["api_mode"] = "v2_oauth"
    settings["warcraft_logs"].setdefault("v2_oauth", {})
    settings["warcraft_logs"]["v2_oauth"]["client_id"] = credentials.client_id
    settings["warcraft_logs"]["v2_oauth"]["client_secret"] = credentials.client_secret

    return save_global_settings(settings)


def reset_saved_v2_credentials() -> bool:
    settings = load_global_settings()
    wcl = settings.get("warcraft_logs", {})

    removed = False

    if "v2_oauth" in wcl:
        del wcl["v2_oauth"]
        removed = True

    for key_name in ["client_id", "client_secret", "v2_token"]:
        if key_name in wcl:
            del wcl[key_name]
            removed = True

    if removed:
        settings["warcraft_logs"] = wcl
        save_global_settings(settings)

    return removed


def prompt_for_v2_credentials() -> WCLV2Credentials:
    print()
    print("No Warcraft Logs v2 OAuth credentials found.")
    print("Paste your Warcraft Logs / Archon API Client ID and Client Secret below.")
    print("They will be saved globally for future versions of this tracker.")
    print()
    print("These are not the same as the old v1 API key.")
    print()

    client_id = input("WCL v2 Client ID: ").strip()
    client_secret = input("WCL v2 Client Secret: ").strip()

    if not client_id or not client_secret:
        raise SettingsError("WCL v2 Client ID and Client Secret cannot be blank.")

    return WCLV2Credentials(client_id=client_id, client_secret=client_secret)


def resolve_wcl_v2_credentials(
    env_client_id: str | None = None,
    env_client_secret: str | None = None,
    use_global_settings: bool = True,
) -> WCLV2Credentials:
    if env_client_id and env_client_secret:
        return WCLV2Credentials(client_id=env_client_id, client_secret=env_client_secret)

    if use_global_settings:
        saved = get_v2_credentials_from_settings()
        if saved:
            return saved

        entered = prompt_for_v2_credentials()
        path = save_v2_credentials_to_settings(entered)
        print(f"Saved Warcraft Logs v2 OAuth credentials to: {path}")
        return entered

    raise SettingsError("No WCL_CLIENT_ID/WCL_CLIENT_SECRET found.")


def get_v2_token_from_settings() -> WCLV2Token | None:
    settings = load_global_settings()
    wcl = settings.get("warcraft_logs", {})
    token = wcl.get("v2_token", {})

    access_token = token.get("access_token")
    expires_at_utc = token.get("expires_at_utc")
    token_type = token.get("token_type", "Bearer")

    if access_token and expires_at_utc:
        try:
            return WCLV2Token(
                access_token=access_token,
                expires_at_utc=int(expires_at_utc),
                token_type=token_type,
            )
        except ValueError:
            return None

    return None


def save_v2_token_to_settings(token: WCLV2Token) -> Path:
    settings = load_global_settings()

    settings.setdefault("schema_version", 1)
    settings.setdefault("warcraft_logs", {})
    settings["warcraft_logs"]["v2_token"] = {
        "access_token": token.access_token,
        "expires_at_utc": token.expires_at_utc,
        "token_type": token.token_type,
    }

    return save_global_settings(settings)


def get_global_database_dir() -> Path:
    return get_global_settings_dir() / "database"


def clear_global_database_cache(delete_database_file: bool = False) -> tuple[bool, Path]:
    """
    Clear SQLite-backed comparison/schedule caches.

    By default this clears cache/result tables but keeps the database file so
    schema/settings survive. If delete_database_file=True, it deletes the
    whole database folder instead.
    """
    import sqlite3

    db_root = get_global_database_dir()
    db_path = db_root / "comparison.sqlite"

    if delete_database_file:
        if not db_root.exists():
            return False, db_root
        shutil.rmtree(db_root)
        return True, db_root

    if not db_path.exists():
        return False, db_path

    tables = [
        "raiderio_discovery_cache",
        "schedule_report_cache",
        "schedule_scan_results",
        "schedule_raid_nights",
        "endboss_kill_cache",
    ]

    conn = sqlite3.connect(db_path)
    try:
        for table in tables:
            try:
                conn.execute(f"DELETE FROM {table}")
            except sqlite3.OperationalError:
                # Older DB may not have every table yet.
                pass
        conn.commit()
    finally:
        conn.close()

    return True, db_path


def clear_all_global_caches() -> list[tuple[str, bool, Path]]:
    removed_file_cache, file_cache_path = clear_global_cache()
    removed_db_cache, db_cache_path = clear_global_database_cache(delete_database_file=False)
    return [
        ("file cache", removed_file_cache, file_cache_path),
        ("sqlite cache", removed_db_cache, db_cache_path),
    ]
