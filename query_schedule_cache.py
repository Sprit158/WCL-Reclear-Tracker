from __future__ import annotations

import argparse

from main import load_config_early
from schedule_database import connect_schedule_db
from settings_manager import get_guild_profile_from_settings


def table_cell(value, width: int, align: str = "left") -> str:
    text = "-" if value is None or value == "" else str(value)
    if len(text) > width:
        text = text[: max(1, width - 1)] + "~"
    return text.rjust(width) if align == "right" else text.ljust(width)


def number_cell(value) -> str:
    if value is None:
        return "-"
    return f"{float(value):.2f}".rstrip("0").rstrip(".")


def render_table(rows, own_profile=None) -> str:
    columns = [
        ("Rank", "rank", 4, "right"),
        ("Guild", "guild", 19, "left"),
        ("Realm", "realm", 9, "left"),
        ("2d?", "is_likely_two_day", 3, "left"),
        ("Avg", "average_raid_days_per_active_week", 5, "right"),
        ("M1", "first_month_average_raid_days", 5, "right"),
        ("Med", "inferred_days_per_week", 5, "right"),
        ("Hrs", "logged_window_hours_per_week", 5, "right"),
        ("Wks", "active_weeks", 4, "right"),
        ("Common days", "inferred_raid_days", 14, "left"),
    ]
    numeric = {
        "average_raid_days_per_active_week",
        "first_month_average_raid_days",
        "inferred_days_per_week",
        "logged_window_hours_per_week",
    }
    separator = "+" + "+".join("-" * (width + 2) for _, _, width, _ in columns) + "+"
    header = "| " + " | ".join(table_cell(title, width) for title, _, width, _ in columns) + " |"
    lines = [separator, header, separator]
    for row in rows:
        cells = []
        for _, key, width, align in columns:
            if key == "is_likely_two_day":
                confidence = str(row["schedule_confidence"] or "").strip().lower()
                value = "?" if confidence in {"unverified", "error"} else ("Yes" if row[key] else "No")
            else:
                value = number_cell(row[key]) if key in numeric else row[key]
            if key == "guild" and own_profile and (
                str(row["guild"]).strip().lower() == own_profile.name.strip().lower()
                and str(row["realm"]).strip().lower() == own_profile.realm.strip().lower()
                and str(row["region"]).strip().upper() == own_profile.region.strip().upper()
            ):
                value = f"{value} (you)"
            cells.append(table_cell(value, width, align))
        lines.append("| " + " | ".join(cells) + " |")
    lines.append(separator)
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Query cached schedule scan results from comparison.sqlite.")
    parser.add_argument("--two-day", action="store_true", help="Show likely 2-day guilds.")
    parser.add_argument("--days", help="Filter inferred raid days text, e.g. Mon,Wed or Wed.")
    parser.add_argument("--min-hours", type=float, default=None)
    parser.add_argument("--max-hours", type=float, default=None)
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()

    config = load_config_early()
    conn = connect_schedule_db(config)
    own_profile = get_guild_profile_from_settings()

    where = []
    params = []

    if args.two_day:
        if own_profile:
            where.append(
                "(is_likely_two_day = 1 OR "
                "(LOWER(guild) = LOWER(?) AND LOWER(realm) = LOWER(?) AND UPPER(region) = UPPER(?)))"
            )
            params.extend([own_profile.name, own_profile.realm, own_profile.region])
        else:
            where.append("is_likely_two_day = 1")
    if args.days:
        for day in args.days.replace(",", " ").split():
            where.append("inferred_raid_days LIKE ?")
            params.append(f"%{day}%")
    if args.min_hours is not None:
        where.append("inferred_hours_per_week >= ?")
        params.append(args.min_hours)
    if args.max_hours is not None:
        where.append("inferred_hours_per_week <= ?")
        params.append(args.max_hours)

    sql = """
        SELECT
            rank,
            guild,
            realm,
            region,
            inferred_days_per_week,
            average_raid_days_per_active_week,
            first_month_average_raid_days,
            logged_window_hours_per_week,
            inferred_hours_per_week,
            inferred_raid_days,
            active_weeks,
            raid_nights_found,
            is_likely_two_day,
            reason
        FROM schedule_scan_results
    """

    if where:
        sql += " WHERE " + " AND ".join(where)

    sql += " ORDER BY rank LIMIT ?"
    params.append(args.limit)

    rows = conn.execute(sql, params).fetchall()

    if not rows:
        print("No cached schedule results matched.")
        if own_profile:
            print("Your saved guild is not in the schedule cache. Run option 2 to add and scan it.")
        conn.close()
        return

    own_row_present = bool(
        own_profile and any(
            str(row["guild"]).strip().lower() == own_profile.name.strip().lower()
            and str(row["realm"]).strip().lower() == own_profile.realm.strip().lower()
            and str(row["region"]).strip().upper() == own_profile.region.strip().upper()
            for row in rows
        )
    )

    print(render_table(rows, own_profile))
    if own_profile and not own_row_present:
        print()
        print("NOTE: Your saved guild is missing from the schedule cache. Run option 2 to add and scan it.")
    print()
    print("Avg/wk = recurring core raid days plus overtime days divided by active weeks")
    print("        ambiguous/rotating schedules use their observed average instead of guessed overtime")
    print("M1/wk  = average raid days across the first four reset weeks (zero-night weeks included)")
    print("Med/wk = median raid nights per active week; Hours = median logged-window hours per week")

    conn.close()


if __name__ == "__main__":
    main()
