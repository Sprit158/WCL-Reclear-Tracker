from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import csv
import re
import shutil

from settings_manager import get_global_settings_dir, get_guild_profile_from_settings


JsonDict = dict[str, Any]


@dataclass(slots=True)
class WowProgressBackupMatch:
    rank: int | None
    guild: str
    region: str
    realm: str
    progress: str
    recruiting_flag: str
    declared_raids_week: str
    source_type: str
    notes: str
    match_quality: str


def normalise_text(value: str | None) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("’", "'").replace("`", "'").replace("´", "'")
    text = text.replace("–", "-").replace("—", "-")
    text = re.sub(r"\s+", " ", text)
    return text


def normalise_realm(value: str | None) -> str:
    text = normalise_text(value)
    text = text.replace("_", " ").replace("-", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalise_region(value: str | None) -> str:
    text = normalise_text(value).upper()
    if text.startswith("EU"):
        return "EU"
    if text.startswith("US"):
        return "US"
    if text.startswith("OC") or text.startswith("OCE"):
        return "OC"
    return text


def normalise_guild(value: str | None) -> str:
    text = normalise_text(value)
    # Remove WoWProgress recruitment marker if accidentally included.
    text = re.sub(r"\s*\(r\)\s*$", "", text)
    return text


def backup_path_from_config(config: JsonDict) -> Path:
    settings = config.get("comparison", {}).get("wowprogress_backup", {})
    raw = str(settings.get("backup_file", "data/wowprogress_1_2_day_backup.csv"))
    path = Path(raw)
    if path.is_absolute():
        return path
    return Path(__file__).resolve().parent / path


def global_backup_path(config: JsonDict) -> Path:
    return get_global_settings_dir() / "data" / backup_path_from_config(config).name


def ensure_wowprogress_backup(config: JsonDict) -> Path:
    """Find the local dataset and persist it outside versioned app folders."""

    preferred = backup_path_from_config(config)
    durable = global_backup_path(config)

    if preferred.exists() and preferred.stat().st_size > 0:
        try:
            durable.parent.mkdir(parents=True, exist_ok=True)
            if not durable.exists() or durable.stat().st_size < preferred.stat().st_size:
                shutil.copy2(preferred, durable)
        except OSError:
            pass
        return preferred

    if durable.exists() and durable.stat().st_size > 0:
        try:
            preferred.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(durable, preferred)
            return preferred
        except OSError:
            return durable

    # Recover automatically when the user unpacked a new version beside an old
    # one. Keep the search shallow so startup remains fast and predictable.
    app_root = Path(__file__).resolve().parent
    filename = preferred.name
    candidates: list[Path] = []
    for pattern_root, pattern in [
        (app_root.parent, f"*/data/{filename}"),
        (app_root.parent.parent, f"*/build_v*/data/{filename}"),
        (app_root.parent.parent, f"build_v*/data/{filename}"),
    ]:
        try:
            candidates.extend(pattern_root.glob(pattern))
        except OSError:
            pass

    candidates = [
        path for path in candidates
        if path != preferred and path.is_file() and path.stat().st_size > 0
    ]
    if candidates:
        source = max(candidates, key=lambda path: (path.stat().st_size, path.stat().st_mtime))
        try:
            preferred.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, preferred)
            durable.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, durable)
            return preferred
        except OSError:
            return source

    return preferred


def load_wowprogress_backup(config: JsonDict) -> list[dict[str, str]]:
    settings = config.get("comparison", {}).get("wowprogress_backup", {})
    if not settings.get("enabled", True):
        return []

    path = ensure_wowprogress_backup(config)
    if not path.exists():
        return []

    rows: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append({str(k): "" if v is None else str(v) for k, v in row.items()})
    return rows


def find_wowprogress_backup_match(
    config: JsonDict,
    *,
    guild: str,
    region: str,
    realm: str,
    backup_rows: list[dict[str, str]] | None = None,
) -> WowProgressBackupMatch | None:
    rows = backup_rows if backup_rows is not None else load_wowprogress_backup(config)
    if not rows:
        return None

    target_guild = normalise_guild(guild)
    target_region = normalise_region(region)
    target_realm = normalise_realm(realm)

    for row in rows:
        row_guild = normalise_guild(row.get("Guild"))
        row_region = normalise_region(row.get("Region"))
        row_realm = normalise_realm(row.get("Realm"))

        if row_guild == target_guild and row_region == target_region and row_realm == target_realm:
            try:
                rank = int(str(row.get("World Rank", "")).strip())
            except Exception:
                rank = None

            return WowProgressBackupMatch(
                rank=rank,
                guild=row.get("Guild", ""),
                region=row.get("Region", ""),
                realm=row.get("Realm", ""),
                progress=row.get("Progress", ""),
                recruiting_flag=row.get("Recruiting flag", ""),
                declared_raids_week=row.get("Declared raids/week", "1-2"),
                source_type=row.get("Source Type", "WoWProgress screenshot backup"),
                notes=row.get("Notes", ""),
                match_quality="exact_guild_region_realm",
            )

    return None


def backup_match_to_schedule_result(match: WowProgressBackupMatch) -> dict[str, Any]:
    return {
        "wowprogress_backup_match": True,
        "wowprogress_rank": match.rank,
        "wowprogress_declared_raids_week": match.declared_raids_week,
        "wowprogress_match_quality": match.match_quality,
        "wowprogress_backup_notes": match.notes,
    }


def rows_from_wowprogress_backup_for_schedule_scan(config: JsonDict) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Build schedule-scan rows from the local WoWProgress backup file.

    This is used to scan every declared 1-2 day guild above the user's guild,
    rather than scanning an arbitrary Raider.IO discovery window.
    """
    settings = config.get("comparison", {}).get("wowprogress_backup", {})
    rows = load_wowprogress_backup(config)

    saved_profile = get_guild_profile_from_settings()
    configured_guild = str(settings.get("own_guild", "") or "").strip()
    configured_realm = str(settings.get("own_realm", "") or "").strip()
    configured_region = str(settings.get("own_region", "") or "").strip()
    profile_is_source = bool(saved_profile and not configured_guild and not configured_realm)
    own_guild = normalise_guild(configured_guild or (saved_profile.name if saved_profile else ""))
    own_realm = normalise_realm(configured_realm or (saved_profile.realm if saved_profile else ""))
    own_region = normalise_region(
        saved_profile.region if profile_is_source else (configured_region or (saved_profile.region if saved_profile else "EU"))
    )

    own_rank = None
    try:
        own_rank = int(settings.get("own_world_rank") or 0) or None
    except Exception:
        own_rank = None

    # Prefer the rank from the backup row if present.
    for row in rows:
        if (
            normalise_guild(row.get("Guild")) == own_guild
            and normalise_region(row.get("Region")) == own_region
            and normalise_realm(row.get("Realm")) == own_realm
        ):
            try:
                own_rank = int(str(row.get("World Rank", "")).strip())
            except Exception:
                pass
            break

    include_only_above = bool(settings.get("include_only_above_own", True))
    include_own = bool(settings.get("include_own", False))
    max_rows = int(settings.get("max_backup_guilds_used", 1000))
    raw_region_filter = str(settings.get("region_filter", "EU") or "").strip()
    if raw_region_filter.lower() in {"", "all", "world", "any", "*"}:
        region_filter = ""
    else:
        region_filter = normalise_region(raw_region_filter)

    output: list[dict[str, Any]] = []
    skipped_below = 0
    skipped_region = 0
    skipped_bad_rank = 0

    for row in rows:
        try:
            rank = int(str(row.get("World Rank", "")).strip())
        except Exception:
            skipped_bad_rank += 1
            continue

        guild = str(row.get("Guild", "")).strip()
        region = normalise_region(row.get("Region", ""))
        realm = str(row.get("Realm", "")).strip()

        if not guild or not realm or not region:
            continue

        if region_filter and region != region_filter:
            skipped_region += 1
            continue

        if own_rank is not None and include_only_above:
            if include_own:
                if rank > own_rank:
                    skipped_below += 1
                    continue
            else:
                if rank >= own_rank:
                    skipped_below += 1
                    continue

        output.append({
            "guild": guild,
            "realm": realm,
            "region": region,
            "rank": rank,
            "endboss_kill_timestamp_ms": None,
            "endboss_kill_date": "",
            "endboss_kill_source": "",
            "source": "wowprogress_backup_declared_1_2",
            "wowprogress_declared_raids_week": str(row.get("Declared raids/week", "1-2")),
            "wowprogress_progress": str(row.get("Progress", "")),
            "wowprogress_notes": str(row.get("Notes", "")),
        })

        if len(output) >= max_rows:
            break

    output.sort(key=lambda r: (r.get("rank") is None, int(r.get("rank") or 999999), str(r.get("guild", "")).lower()))

    resolved_backup = ensure_wowprogress_backup(config)
    meta = {
        "source": "wowprogress_backup_declared_1_2",
        "own_rank": own_rank,
        "rows_in_backup": len(rows),
        "selected": len(output),
        "skipped_below_or_equal_own": skipped_below,
        "skipped_region": skipped_region,
        "skipped_bad_rank": skipped_bad_rank,
        "region_filter": region_filter,
        "raw_region_filter": raw_region_filter,
        "backup_file": str(resolved_backup),
        "backup_file_exists": resolved_backup.exists(),
        "own_guild": configured_guild or (saved_profile.name if saved_profile else ""),
        "own_realm": configured_realm or (saved_profile.realm if saved_profile else ""),
        "own_region": own_region,
    }
    return output, meta
