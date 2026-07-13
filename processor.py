from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Iterable
import re


JsonDict = dict[str, Any]

REPORT_CODE_RE = re.compile(r"(?:warcraftlogs\.com/reports/)?([A-Za-z0-9]+)")


@dataclass(slots=True)
class FightRow:
    report_code: str
    report_title: str
    raid_date_utc: str
    fight_id: int
    encounter_id: int
    boss_name: str
    kill: bool
    difficulty: int
    start_ms: int
    end_ms: int
    absolute_start_ms: int
    absolute_end_ms: int
    duration_seconds: float
    duration_minutes: float
    duration_hours: float
    window_segment_seconds: float
    window_segment_minutes: float
    window_segment_hours: float
    classification: str
    phase: str


def normalise_boss_name(name: str) -> str:
    value = name.strip().lower()
    value = value.replace("’", "'").replace("`", "'")
    value = re.sub(r"\s+", " ", value)
    value = value.replace(",", "")
    return value


def canonical_boss_name(name: str) -> str:
    aliases = {
        normalise_boss_name("Chimaerus the Undreamt God"): "Chimaerus, the Undreamt God",
        normalise_boss_name("Chimaerus, the Undreamt God"): "Chimaerus, the Undreamt God",
        normalise_boss_name("Belo’ren, Child of Al’ar"): "Belo'ren, Child of Al'ar",
        normalise_boss_name("Belo'ren, Child of Al'ar"): "Belo'ren, Child of Al'ar",
    }
    return aliases.get(normalise_boss_name(name), name)


def extract_report_code(line: str) -> str | None:
    line = line.strip()

    if not line or line.startswith("#"):
        return None

    match = REPORT_CODE_RE.search(line)

    if not match:
        return None

    return match.group(1)


def load_report_codes(path: str = "reports.txt") -> list[str]:
    with open(path, "r", encoding="utf-8") as f:
        raw_codes = [
            code
            for line in f
            if (code := extract_report_code(line))
        ]

    return list(dict.fromkeys(raw_codes))


def boss_allowed(boss_name: str, boss_filter: JsonDict | None) -> bool:
    if not boss_filter or not boss_filter.get("enabled", False):
        return True

    name_key = normalise_boss_name(boss_name)
    excluded = {normalise_boss_name(x) for x in boss_filter.get("excluded_bosses", [])}
    allowed = {normalise_boss_name(x) for x in boss_filter.get("allowed_bosses", [])}

    if name_key in excluded:
        return False

    if boss_filter.get("mode") == "allow_list" and allowed:
        return name_key in allowed

    return True


def calculate_window_segments(
    rows: list[FightRow],
    max_gap_minutes: int = 45,
) -> None:
    """
    Adds true raid-window style time to each fight row.

    The first pull of a date/report segment gets only its pull duration.
    For later pulls, the time from previous fight end to this fight end is assigned
    to the current fight classification, provided the gap is not excessive.
    """

    sorted_rows = sorted(rows, key=lambda r: (r.raid_date_utc, r.absolute_start_ms, r.absolute_end_ms))

    previous_by_date: dict[str, FightRow] = {}
    max_gap_ms = max_gap_minutes * 60 * 1000

    for row in sorted_rows:
        previous = previous_by_date.get(row.raid_date_utc)

        if previous is None:
            segment_ms = row.absolute_end_ms - row.absolute_start_ms
        else:
            gap_ms = row.absolute_start_ms - previous.absolute_end_ms

            if gap_ms < 0:
                # Overlap from merged/duplicate reports. Do not count negative/overlap downtime.
                segment_ms = row.absolute_end_ms - row.absolute_start_ms
            elif gap_ms > max_gap_ms:
                # Treat a very large gap as a separate session.
                segment_ms = row.absolute_end_ms - row.absolute_start_ms
            else:
                segment_ms = row.absolute_end_ms - previous.absolute_end_ms

        segment_seconds = max(0, segment_ms / 1000)
        row.window_segment_seconds = segment_seconds
        row.window_segment_minutes = segment_seconds / 60
        row.window_segment_hours = segment_seconds / 3600

        previous_by_date[row.raid_date_utc] = row


def classify_reports(
    reports: Iterable[JsonDict],
    mythic_difficulty: int = 5,
    minimum_fight_seconds: int = 10,
    boss_filter: JsonDict | None = None,
    time_config: JsonDict | None = None,
) -> list[FightRow]:
    """
    Classifies Mythic boss fights across reports in chronological order.

    Logic:
    - Only allowed bosses are included when boss_filter is enabled.
    - A boss is Progression until the first kill is logged.
    - The first kill itself is still Progression.
    - Future pulls of that boss are Reclear.
    """

    sorted_reports = sorted(reports, key=lambda r: r["startTime"])

    boss_killed_before: set[int] = set()
    rows: list[FightRow] = []

    for report in sorted_reports:
        report_code = report["code"]
        report_title = report.get("title", "")
        report_start_ms = int(report["startTime"])

        fights = sorted(report.get("fights", []), key=lambda f: f["startTime"])

        for fight in fights:
            encounter_id = fight.get("encounterID")
            difficulty = fight.get("difficulty")
            fight_id = fight.get("id")
            raw_boss_name = fight.get("name", "Unknown")
            boss_name = canonical_boss_name(raw_boss_name)
            kill = bool(fight.get("kill", False))

            if not encounter_id:
                continue

            if difficulty != mythic_difficulty:
                continue

            if not boss_allowed(boss_name, boss_filter):
                continue

            start_ms = int(fight["startTime"])
            end_ms = int(fight["endTime"])
            duration_seconds = max(0, (end_ms - start_ms) / 1000)

            if duration_seconds < minimum_fight_seconds:
                continue

            classification = (
                "Reclear"
                if int(encounter_id) in boss_killed_before
                else "Progression"
            )

            # First kill remains progression. Future pulls become reclear.
            if kill:
                boss_killed_before.add(int(encounter_id))

            absolute_start_ms = report_start_ms + start_ms
            absolute_end_ms = report_start_ms + end_ms
            raid_date_utc = datetime.fromtimestamp(
                absolute_start_ms / 1000,
                tz=timezone.utc,
            ).date().isoformat()

            rows.append(
                FightRow(
                    report_code=report_code,
                    report_title=report_title,
                    raid_date_utc=raid_date_utc,
                    fight_id=int(fight_id),
                    encounter_id=int(encounter_id),
                    boss_name=boss_name,
                    kill=kill,
                    difficulty=int(difficulty),
                    start_ms=start_ms,
                    end_ms=end_ms,
                    absolute_start_ms=absolute_start_ms,
                    absolute_end_ms=absolute_end_ms,
                    duration_seconds=duration_seconds,
                    duration_minutes=duration_seconds / 60,
                    duration_hours=duration_seconds / 3600,
                    window_segment_seconds=duration_seconds,
                    window_segment_minutes=duration_seconds / 60,
                    window_segment_hours=duration_seconds / 3600,
                    classification=classification,
                    phase=classification,
                )
            )

    time_config = time_config or {}
    if time_config.get("window_time", True):
        calculate_window_segments(
            rows,
            max_gap_minutes=int(time_config.get("max_gap_minutes_in_window", 45)),
        )

    return rows


def rows_to_dicts(rows: list[FightRow]) -> list[JsonDict]:
    return [asdict(row) for row in rows]
