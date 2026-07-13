from __future__ import annotations

import argparse

from main import load_config_early
from schedule_database import connect_schedule_db


def main() -> None:
    parser = argparse.ArgumentParser(description="Query cached schedule scan results from comparison.sqlite.")
    parser.add_argument("--two-day", action="store_true", help="Show likely 2-day guilds.")
    parser.add_argument("--days", help="Filter inferred raid days text, e.g. Mon,Wed or Wed.")
    parser.add_argument("--min-hours", type=float, default=None)
    parser.add_argument("--max-hours", type=float, default=None)
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()

    config = load_config_early()
    conn = connect_schedule_db(config)

    where = []
    params = []

    if args.two_day:
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
        return

    for row in rows:
        print(
            f"#{row['rank']} {row['guild']}-{row['realm']}-{row['region']} | "
            f"{row['average_raid_days_per_active_week']} avg days/wk | "
            f"{row['inferred_days_per_week']} median nights/wk | "
            f"{row['logged_window_hours_per_week']} logged-window h/wk | "
            f"{row['inferred_raid_days']} | "
            f"two_day={bool(row['is_likely_two_day'])} | "
            f"{row['reason']}"
        )

    conn.close()


if __name__ == "__main__":
    main()
