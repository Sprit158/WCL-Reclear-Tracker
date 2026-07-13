from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import sqlite3
from datetime import datetime, timezone


JsonDict = dict[str, Any]


SCHEMA_VERSION = 1


def connect_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    initialise_database(conn)
    return conn


def initialise_database(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS guilds (
            guild_key TEXT PRIMARY KEY,
            guild TEXT NOT NULL,
            realm TEXT NOT NULL,
            region TEXT NOT NULL,
            declared_days_per_week REAL,
            declared_hours_per_week REAL,
            schedule_source TEXT,
            created_at_utc TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS guild_summaries (
            guild_key TEXT PRIMARY KEY,
            guild TEXT NOT NULL,
            realm TEXT NOT NULL,
            region TEXT NOT NULL,
            declared_days_per_week REAL,
            declared_hours_per_week REAL,
            schedule_source TEXT,
            inferred_days_per_week REAL,
            inferred_hours_per_week REAL,
            inferred_schedule_source TEXT,
            schedule_days_per_week REAL,
            schedule_hours_per_week REAL,
            final_schedule_source TEXT,
            raid_days INTEGER,
            active_weeks INTEGER,
            total_window_hours REAL,
            progression_window_hours REAL,
            reclear_window_hours REAL,
            reclear_tax_percent REAL,
            total_pull_hours REAL,
            progression_pull_hours REAL,
            reclear_pull_hours REAL,
            total_downtime_hours REAL,
            pull_uptime_percent REAL,
            total_pulls INTEGER,
            progression_pulls INTEGER,
            reclear_pulls INTEGER,
            pulls_per_hour REAL,
            reclear_wipes INTEGER,
            reclear_wipe_rate_percent REAL,
            bosses_killed INTEGER,
            complete_tier INTEGER,
            status TEXT,
            last_processed_at_utc TEXT NOT NULL
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS guild_boss_summaries (
            guild_key TEXT NOT NULL,
            boss_name TEXT NOT NULL,
            kill_date TEXT,
            boss_wall_percent REAL,
            progression_pulls INTEGER,
            reclear_pulls INTEGER,
            total_pulls INTEGER,
            progression_window_hours REAL,
            reclear_window_hours REAL,
            total_window_hours REAL,
            reclear_wipes INTEGER,
            PRIMARY KEY (guild_key, boss_name)
        )
        """
    )

    conn.execute("INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)", ("schema_version", str(SCHEMA_VERSION)))
    conn.commit()


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def guild_key(guild: str, realm: str, region: str) -> str:
    return f"{region.strip().upper()}::{realm.strip().lower()}::{guild.strip().lower()}"


def upsert_guild(
    conn: sqlite3.Connection,
    guild: str,
    realm: str,
    region: str,
    declared_days_per_week: float | None = None,
    declared_hours_per_week: float | None = None,
    schedule_source: str | None = None,
) -> str:
    key = guild_key(guild, realm, region)
    current = now_utc()
    existing = conn.execute("SELECT guild_key FROM guilds WHERE guild_key = ?", (key,)).fetchone()

    if existing:
        conn.execute(
            """
            UPDATE guilds
            SET guild = ?, realm = ?, region = ?,
                declared_days_per_week = COALESCE(?, declared_days_per_week),
                declared_hours_per_week = COALESCE(?, declared_hours_per_week),
                schedule_source = COALESCE(?, schedule_source),
                updated_at_utc = ?
            WHERE guild_key = ?
            """,
            (guild, realm, region.upper(), declared_days_per_week, declared_hours_per_week, schedule_source, current, key),
        )
    else:
        conn.execute(
            """
            INSERT INTO guilds (
                guild_key, guild, realm, region,
                declared_days_per_week, declared_hours_per_week, schedule_source,
                created_at_utc, updated_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (key, guild, realm, region.upper(), declared_days_per_week, declared_hours_per_week, schedule_source, current, current),
        )

    conn.commit()
    return key


def summary_exists(conn: sqlite3.Connection, key: str) -> bool:
    row = conn.execute("SELECT guild_key FROM guild_summaries WHERE guild_key = ?", (key,)).fetchone()
    return row is not None


def save_guild_summary(conn: sqlite3.Connection, summary: JsonDict) -> None:
    columns = [
        "guild_key", "guild", "realm", "region",
        "declared_days_per_week", "declared_hours_per_week", "schedule_source",
        "inferred_days_per_week", "inferred_hours_per_week", "inferred_schedule_source",
        "schedule_days_per_week", "schedule_hours_per_week", "final_schedule_source",
        "raid_days", "active_weeks",
        "total_window_hours", "progression_window_hours", "reclear_window_hours",
        "reclear_tax_percent",
        "total_pull_hours", "progression_pull_hours", "reclear_pull_hours",
        "total_downtime_hours", "pull_uptime_percent",
        "total_pulls", "progression_pulls", "reclear_pulls", "pulls_per_hour",
        "reclear_wipes", "reclear_wipe_rate_percent",
        "bosses_killed", "complete_tier", "status", "last_processed_at_utc",
    ]

    values = [summary.get(col) for col in columns]
    placeholders = ", ".join(["?"] * len(columns))
    update_clause = ", ".join([f"{col}=excluded.{col}" for col in columns if col != "guild_key"])

    conn.execute(
        f"""
        INSERT INTO guild_summaries ({", ".join(columns)})
        VALUES ({placeholders})
        ON CONFLICT(guild_key) DO UPDATE SET {update_clause}
        """,
        values,
    )
    conn.commit()


def save_boss_summaries(conn: sqlite3.Connection, guild_key_value: str, rows: list[JsonDict]) -> None:
    conn.execute("DELETE FROM guild_boss_summaries WHERE guild_key = ?", (guild_key_value,))

    for row in rows:
        conn.execute(
            """
            INSERT INTO guild_boss_summaries (
                guild_key, boss_name, kill_date, boss_wall_percent,
                progression_pulls, reclear_pulls, total_pulls,
                progression_window_hours, reclear_window_hours, total_window_hours,
                reclear_wipes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild_key_value,
                row.get("boss_name"),
                row.get("kill_date"),
                row.get("boss_wall_percent"),
                int(row.get("progression_pulls", 0) or 0),
                int(row.get("reclear_pulls", 0) or 0),
                int(row.get("total_pulls", 0) or 0),
                float(row.get("progression_window_hours", 0) or 0),
                float(row.get("reclear_window_hours", 0) or 0),
                float(row.get("total_window_hours", 0) or 0),
                int(row.get("reclear_wipes", 0) or 0),
            ),
        )

    conn.commit()


def load_all_summaries(conn: sqlite3.Connection) -> list[JsonDict]:
    rows = conn.execute("SELECT * FROM guild_summaries ORDER BY guild COLLATE NOCASE").fetchall()
    return [dict(row) for row in rows]
