from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import sqlite3
import time

from settings_manager import get_global_settings_dir


JsonDict = dict[str, Any]


def database_path(config: JsonDict) -> Path:
    comp = config.get("comparison", {})
    filename = comp.get("database_file", "comparison.sqlite")
    if comp.get("database_location", "global_app_data") == "global_app_data":
        return get_global_settings_dir() / "database" / filename
    return Path(filename)


def connect_schedule_db(config: JsonDict) -> sqlite3.Connection:
    path = database_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    init_schedule_db(conn)
    return conn


def ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    existing = {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def run_schema_migrations(conn: sqlite3.Connection) -> None:
    """
    v1.6.11: force-migrate old comparison.sqlite databases.

    CREATE TABLE IF NOT EXISTS does not add new columns to existing tables,
    so every new column used by INSERT statements must be added here.
    """
    ensure_column(conn, "discovered_guilds", "endboss_kill_timestamp_ms", "INTEGER")
    ensure_column(conn, "discovered_guilds", "endboss_kill_date", "TEXT")
    ensure_column(conn, "discovered_guilds", "endboss_kill_source", "TEXT")

    ensure_column(conn, "schedule_scan_results", "average_raid_days_per_active_week", "REAL")
    ensure_column(conn, "schedule_scan_results", "logged_window_hours_per_week", "REAL")
    ensure_column(conn, "schedule_scan_results", "candidate_needs_deep_time_review", "INTEGER DEFAULT 0")
    ensure_column(conn, "schedule_scan_results", "progression_cutoff_date", "TEXT")
    ensure_column(conn, "schedule_scan_results", "progression_cutoff_source", "TEXT")
    ensure_column(conn, "schedule_scan_results", "reports_after_cutoff_excluded", "INTEGER DEFAULT 0")
    ensure_column(conn, "schedule_scan_results", "first_month_average_raid_days", "REAL")


def init_schedule_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS discovered_guilds (
            guild_key TEXT PRIMARY KEY,
            guild TEXT NOT NULL,
            realm TEXT NOT NULL,
            region TEXT NOT NULL,
            rank INTEGER,
            source TEXT,
            url TEXT,
            endboss_kill_timestamp_ms INTEGER,
            endboss_kill_date TEXT,
            endboss_kill_source TEXT,
            discovered_at_unix INTEGER NOT NULL,
            updated_at_unix INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS raiderio_discovery_cache (
            cache_key TEXT PRIMARY KEY,
            raid_slug TEXT NOT NULL,
            difficulty TEXT NOT NULL,
            region TEXT NOT NULL,
            max_rank INTEGER NOT NULL,
            guild_count INTEGER NOT NULL,
            guilds_json TEXT NOT NULL,
            fetched_at_unix INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS endboss_kill_cache (
            guild_key TEXT PRIMARY KEY,
            kill_timestamp_ms INTEGER,
            kill_date TEXT,
            source TEXT,
            checked_report_count INTEGER NOT NULL,
            found INTEGER NOT NULL,
            updated_at_unix INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS schedule_report_cache (
            guild_key TEXT NOT NULL,
            season_start_ms INTEGER NOT NULL,
            season_end_ms INTEGER NOT NULL,
            report_count INTEGER NOT NULL,
            reports_json TEXT NOT NULL,
            source TEXT NOT NULL,
            fetched_at_unix INTEGER NOT NULL,
            PRIMARY KEY (guild_key, season_start_ms, season_end_ms)
        );

        CREATE TABLE IF NOT EXISTS schedule_scan_results (
            guild_key TEXT PRIMARY KEY,
            guild TEXT NOT NULL,
            realm TEXT NOT NULL,
            region TEXT NOT NULL,
            rank INTEGER,
            reports_found INTEGER NOT NULL,
            reports_used_for_schedule INTEGER NOT NULL,
            progression_cutoff_date TEXT,
            progression_cutoff_source TEXT,
            reports_after_cutoff_excluded INTEGER DEFAULT 0,
            raid_nights_found INTEGER NOT NULL,
            active_weeks INTEGER NOT NULL,
            inferred_days_per_week REAL,
            average_raid_days_per_active_week REAL,
            first_month_average_raid_days REAL,
            logged_window_hours_per_week REAL,
            inferred_hours_per_week REAL,
            inferred_raid_days TEXT,
            is_likely_two_day INTEGER NOT NULL,
            candidate_needs_deep_time_review INTEGER DEFAULT 0,
            schedule_source TEXT,
            schedule_confidence TEXT,
            reason TEXT,
            example_nights TEXT,
            notes TEXT,
            scanned_at_unix INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS schedule_raid_nights (
            guild_key TEXT NOT NULL,
            night_date TEXT NOT NULL,
            weekday TEXT NOT NULL,
            week_start TEXT NOT NULL,
            start_ms INTEGER NOT NULL,
            end_ms INTEGER NOT NULL,
            hours REAL NOT NULL,
            report_count INTEGER NOT NULL,
            report_codes TEXT,
            zone_names TEXT,
            scanned_at_unix INTEGER NOT NULL,
            PRIMARY KEY (guild_key, night_date)
        );

        CREATE INDEX IF NOT EXISTS idx_discovered_rank
            ON discovered_guilds(region, rank);

        CREATE INDEX IF NOT EXISTS idx_schedule_results_two_day
            ON schedule_scan_results(is_likely_two_day, inferred_hours_per_week, inferred_days_per_week);

        CREATE INDEX IF NOT EXISTS idx_schedule_results_days
            ON schedule_scan_results(inferred_raid_days);

        CREATE INDEX IF NOT EXISTS idx_schedule_nights_weekday
            ON schedule_raid_nights(weekday, week_start);

        CREATE VIEW IF NOT EXISTS v_likely_two_day_guilds AS
            SELECT
                guild,
                realm,
                region,
                rank,
                inferred_days_per_week,
                average_raid_days_per_active_week,
                first_month_average_raid_days,
                logged_window_hours_per_week,
                inferred_hours_per_week,
                inferred_raid_days,
                active_weeks,
                raid_nights_found,
                reason
            FROM schedule_scan_results
            WHERE is_likely_two_day = 1
            ORDER BY rank;
        """
    )
    run_schema_migrations(conn)
    conn.commit()


def guild_key(guild: str, realm: str, region: str) -> str:
    return f"{region.strip().upper()}::{realm.strip().lower()}::{guild.strip().lower()}"


def upsert_discovered_guilds(conn: sqlite3.Connection, guilds: list[Any]) -> None:
    now = int(time.time())
    rows = []
    for item in guilds:
        g = getattr(item, "guild", None) or item.get("guild")
        r = getattr(item, "realm", None) or item.get("realm")
        reg = getattr(item, "region", None) or item.get("region")
        if not g or not r or not reg:
            continue
        rows.append(
            (
                guild_key(g, r, reg),
                g,
                r,
                reg.upper(),
                getattr(item, "rank", None) if not isinstance(item, dict) else item.get("rank"),
                getattr(item, "source", None) if not isinstance(item, dict) else item.get("source"),
                getattr(item, "url", None) if not isinstance(item, dict) else item.get("url"),
                getattr(item, "endboss_kill_timestamp_ms", None) if not isinstance(item, dict) else item.get("endboss_kill_timestamp_ms"),
                getattr(item, "endboss_kill_date", None) if not isinstance(item, dict) else item.get("endboss_kill_date"),
                getattr(item, "endboss_kill_source", None) if not isinstance(item, dict) else item.get("endboss_kill_source"),
                now,
                now,
            )
        )

    conn.executemany(
        """
        INSERT INTO discovered_guilds (
            guild_key, guild, realm, region, rank, source, url,
            endboss_kill_timestamp_ms, endboss_kill_date, endboss_kill_source,
            discovered_at_unix, updated_at_unix
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(guild_key) DO UPDATE SET
            guild = excluded.guild,
            realm = excluded.realm,
            region = excluded.region,
            rank = excluded.rank,
            source = excluded.source,
            url = excluded.url,
            endboss_kill_timestamp_ms = excluded.endboss_kill_timestamp_ms,
            endboss_kill_date = excluded.endboss_kill_date,
            endboss_kill_source = excluded.endboss_kill_source,
            updated_at_unix = excluded.updated_at_unix
        """,
        rows,
    )
    conn.commit()


def discovery_cache_key(raid_slug: str, difficulty: str, region: str, max_rank: int, cache_version: str = "v1") -> str:
    return f"{cache_version}::{raid_slug.strip().lower()}::{difficulty.strip().lower()}::{region.strip().lower()}::top{int(max_rank)}"


def get_cached_discovered_guilds(
    conn: sqlite3.Connection,
    raid_slug: str,
    difficulty: str,
    region: str,
    max_rank: int,
    ttl_hours: float,
    cache_version: str = "v1",
) -> list[dict[str, Any]] | None:
    key = discovery_cache_key(raid_slug, difficulty, region, max_rank, cache_version)
    row = conn.execute(
        """
        SELECT guilds_json, fetched_at_unix
        FROM raiderio_discovery_cache
        WHERE cache_key = ?
        """,
        (key,),
    ).fetchone()

    if not row:
        return None

    age_seconds = time.time() - int(row["fetched_at_unix"])
    if ttl_hours >= 0 and age_seconds > ttl_hours * 3600:
        return None

    try:
        return json.loads(row["guilds_json"])
    except Exception:
        return None


def upsert_discovery_cache(
    conn: sqlite3.Connection,
    raid_slug: str,
    difficulty: str,
    region: str,
    max_rank: int,
    guilds: list[Any],
    cache_version: str = "v1",
) -> None:
    key = discovery_cache_key(raid_slug, difficulty, region, max_rank, cache_version)
    now = int(time.time())

    rows = []
    for item in guilds[:max_rank]:
        if isinstance(item, dict):
            rows.append(item)
        else:
            rows.append(
                {
                    "rank": getattr(item, "rank", None),
                    "guild": getattr(item, "guild", ""),
                    "realm": getattr(item, "realm", ""),
                    "region": getattr(item, "region", ""),
                    "source": getattr(item, "source", ""),
                    "url": getattr(item, "url", ""),
                }
            )

    conn.execute(
        """
        INSERT INTO raiderio_discovery_cache (
            cache_key, raid_slug, difficulty, region, max_rank, guild_count, guilds_json, fetched_at_unix
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(cache_key) DO UPDATE SET
            raid_slug = excluded.raid_slug,
            difficulty = excluded.difficulty,
            region = excluded.region,
            max_rank = excluded.max_rank,
            guild_count = excluded.guild_count,
            guilds_json = excluded.guilds_json,
            fetched_at_unix = excluded.fetched_at_unix
        """,
        (
            key,
            raid_slug,
            difficulty,
            region.lower(),
            int(max_rank),
            len(rows),
            json.dumps(rows, ensure_ascii=False),
            now,
        ),
    )
    conn.commit()


def get_schedule_result_statuses(conn: sqlite3.Connection) -> dict[str, str]:
    """
    Returns guild_key -> schedule_confidence/status for already scanned guilds.
    Used by v1.6.8 to skip completed schedule scans and process the next batch.
    """
    rows = conn.execute(
        """
        SELECT guild_key, COALESCE(schedule_confidence, '') AS schedule_confidence
        FROM schedule_scan_results
        """
    ).fetchall()
    return {str(row["guild_key"]): str(row["schedule_confidence"] or "") for row in rows}


def should_skip_existing_schedule_result(
    statuses: dict[str, str],
    guild: str,
    realm: str,
    region: str,
    retry_errors: bool = False,
) -> bool:
    key = guild_key(guild, realm, region)
    if key not in statuses:
        return False
    if retry_errors and statuses.get(key) == "error":
        return False
    return True


def get_cached_endboss_kill(
    conn: sqlite3.Connection,
    guild: str,
    realm: str,
    region: str,
    ttl_hours: float,
) -> dict[str, Any] | None:
    key = guild_key(guild, realm, region)
    row = conn.execute(
        """
        SELECT kill_timestamp_ms, kill_date, source, checked_report_count, found, updated_at_unix
        FROM endboss_kill_cache
        WHERE guild_key = ?
        """,
        (key,),
    ).fetchone()

    if not row:
        return None

    age_seconds = time.time() - int(row["updated_at_unix"])
    if ttl_hours >= 0 and age_seconds > ttl_hours * 3600:
        return None

    return {
        "kill_timestamp_ms": row["kill_timestamp_ms"],
        "kill_date": row["kill_date"] or "",
        "source": row["source"] or "",
        "checked_report_count": int(row["checked_report_count"] or 0),
        "found": bool(row["found"]),
    }


def upsert_endboss_kill_cache(
    conn: sqlite3.Connection,
    guild: str,
    realm: str,
    region: str,
    kill_timestamp_ms: int | None,
    kill_date: str,
    source: str,
    checked_report_count: int,
    found: bool,
) -> None:
    key = guild_key(guild, realm, region)
    now = int(time.time())
    conn.execute(
        """
        INSERT INTO endboss_kill_cache (
            guild_key, kill_timestamp_ms, kill_date, source, checked_report_count, found, updated_at_unix
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(guild_key) DO UPDATE SET
            kill_timestamp_ms = excluded.kill_timestamp_ms,
            kill_date = excluded.kill_date,
            source = excluded.source,
            checked_report_count = excluded.checked_report_count,
            found = excluded.found,
            updated_at_unix = excluded.updated_at_unix
        """,
        (
            key,
            kill_timestamp_ms,
            kill_date,
            source,
            int(checked_report_count),
            1 if found else 0,
            now,
        ),
    )
    conn.commit()


def get_cached_reports(
    conn: sqlite3.Connection,
    guild: str,
    realm: str,
    region: str,
    season_start_ms: int,
    season_end_ms: int,
) -> list[JsonDict] | None:
    row = conn.execute(
        """
        SELECT reports_json
        FROM schedule_report_cache
        WHERE guild_key = ?
          AND season_start_ms = ?
          AND season_end_ms = ?
        """,
        (guild_key(guild, realm, region), season_start_ms, season_end_ms),
    ).fetchone()

    if not row:
        return None

    try:
        return json.loads(row["reports_json"])
    except Exception:
        return None


def get_latest_cached_reports(
    conn: sqlite3.Connection,
    guild: str,
    realm: str,
    region: str,
    season_start_ms: int,
    ttl_hours: float,
) -> list[JsonDict] | None:
    """Return the newest fresh report-list cache despite a moving season end date."""

    row = conn.execute(
        """
        SELECT reports_json, fetched_at_unix
        FROM schedule_report_cache
        WHERE guild_key = ?
          AND season_start_ms = ?
        ORDER BY fetched_at_unix DESC
        LIMIT 1
        """,
        (guild_key(guild, realm, region), season_start_ms),
    ).fetchone()
    if not row:
        return None

    age_seconds = time.time() - int(row["fetched_at_unix"])
    if ttl_hours >= 0 and age_seconds > ttl_hours * 3600:
        return None
    try:
        return json.loads(row["reports_json"])
    except Exception:
        return None


def upsert_report_cache(
    conn: sqlite3.Connection,
    guild: str,
    realm: str,
    region: str,
    season_start_ms: int,
    season_end_ms: int,
    reports: list[JsonDict],
    source: str,
) -> None:
    now = int(time.time())
    conn.execute(
        """
        INSERT INTO schedule_report_cache (
            guild_key, season_start_ms, season_end_ms, report_count, reports_json, source, fetched_at_unix
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(guild_key, season_start_ms, season_end_ms) DO UPDATE SET
            report_count = excluded.report_count,
            reports_json = excluded.reports_json,
            source = excluded.source,
            fetched_at_unix = excluded.fetched_at_unix
        """,
        (
            guild_key(guild, realm, region),
            season_start_ms,
            season_end_ms,
            len(reports),
            json.dumps(reports, ensure_ascii=False),
            source,
            now,
        ),
    )
    conn.commit()


def upsert_schedule_result(conn: sqlite3.Connection, result: Any) -> None:
    now = int(time.time())
    conn.execute(
        """
        INSERT INTO schedule_scan_results (
            guild_key,
            guild,
            realm,
            region,
            rank,
            reports_found,
            reports_used_for_schedule,
            progression_cutoff_date,
            progression_cutoff_source,
            reports_after_cutoff_excluded,
            raid_nights_found,
            active_weeks,
            inferred_days_per_week,
            average_raid_days_per_active_week,
            first_month_average_raid_days,
            logged_window_hours_per_week,
            inferred_hours_per_week,
            inferred_raid_days,
            is_likely_two_day,
            candidate_needs_deep_time_review,
            schedule_source,
            schedule_confidence,
            reason,
            example_nights,
            notes,
            scanned_at_unix
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(guild_key) DO UPDATE SET
            guild = excluded.guild,
            realm = excluded.realm,
            region = excluded.region,
            rank = excluded.rank,
            reports_found = excluded.reports_found,
            reports_used_for_schedule = excluded.reports_used_for_schedule,
            progression_cutoff_date = excluded.progression_cutoff_date,
            progression_cutoff_source = excluded.progression_cutoff_source,
            reports_after_cutoff_excluded = excluded.reports_after_cutoff_excluded,
            raid_nights_found = excluded.raid_nights_found,
            active_weeks = excluded.active_weeks,
            inferred_days_per_week = excluded.inferred_days_per_week,
            average_raid_days_per_active_week = excluded.average_raid_days_per_active_week,
            first_month_average_raid_days = excluded.first_month_average_raid_days,
            logged_window_hours_per_week = excluded.logged_window_hours_per_week,
            inferred_hours_per_week = excluded.inferred_hours_per_week,
            inferred_raid_days = excluded.inferred_raid_days,
            is_likely_two_day = excluded.is_likely_two_day,
            candidate_needs_deep_time_review = excluded.candidate_needs_deep_time_review,
            schedule_source = excluded.schedule_source,
            schedule_confidence = excluded.schedule_confidence,
            reason = excluded.reason,
            example_nights = excluded.example_nights,
            notes = excluded.notes,
            scanned_at_unix = excluded.scanned_at_unix
        """,
        (
            guild_key(result.guild, result.realm, result.region),
            result.guild,
            result.realm,
            result.region,
            result.rank,
            result.reports_found,
            result.reports_used_for_schedule,
            getattr(result, "progression_cutoff_date", None),
            getattr(result, "progression_cutoff_source", None),
            getattr(result, "reports_after_cutoff_excluded", 0),
            result.raid_nights_found,
            result.active_weeks,
            result.inferred_days_per_week,
            getattr(result, "average_raid_days_per_active_week", None),
            getattr(result, "first_month_average_raid_days", None),
            getattr(result, "logged_window_hours_per_week", None),
            result.inferred_hours_per_week,
            result.inferred_raid_days,
            1 if result.is_likely_two_day else 0,
            1 if getattr(result, "candidate_needs_deep_time_review", False) else 0,
            result.schedule_source,
            result.schedule_confidence,
            result.reason,
            result.example_nights,
            result.notes,
            now,
        ),
    )
    conn.commit()


def replace_raid_nights(conn: sqlite3.Connection, guild: str, realm: str, region: str, nights: list[Any]) -> None:
    key = guild_key(guild, realm, region)
    now = int(time.time())

    conn.execute("DELETE FROM schedule_raid_nights WHERE guild_key = ?", (key,))

    conn.executemany(
        """
        INSERT INTO schedule_raid_nights (
            guild_key,
            night_date,
            weekday,
            week_start,
            start_ms,
            end_ms,
            hours,
            report_count,
            report_codes,
            zone_names,
            scanned_at_unix
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                key,
                night.date,
                night.weekday,
                night.week_start,
                night.start_ms,
                night.end_ms,
                night.hours,
                night.report_count,
                night.report_codes,
                night.zone_names,
                now,
            )
            for night in nights
        ],
    )
    conn.commit()


def query_likely_two_day(conn: sqlite3.Connection, limit: int = 100) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM v_likely_two_day_guilds
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]
