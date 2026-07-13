from __future__ import annotations

from pathlib import Path
import shutil

import pandas as pd


def safe_percent(numerator: float, denominator: float) -> float:
    return (numerator / denominator * 100) if denominator else 0


def safe_rate(numerator: float, denominator: float) -> float:
    return (numerator / denominator) if denominator else 0


def build_summaries(fight_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if fight_df.empty:
        overall_summary = pd.DataFrame(
            [
                {
                    "raid_days": 0,
                    "total_window_hours": 0,
                    "total_pull_hours": 0,
                    "total_downtime_hours": 0,
                    "reclear_tax_percent": 0,
                    "pull_uptime_percent": 0,
                    "pulls_per_hour": 0,
                    "total_pulls": 0,
                    "total_kills": 0,
                }
            ]
        )
        return pd.DataFrame(), overall_summary, pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    df = fight_df.copy()
    df["downtime_hours"] = (df["window_segment_hours"] - df["duration_hours"]).clip(lower=0)
    df["downtime_minutes"] = df["downtime_hours"] * 60

    # Night summary
    pull_grouped = (
        df.groupby(["raid_date_utc", "classification"])["duration_hours"]
        .sum()
        .unstack(fill_value=0)
    )
    pull_grouped["Progression"] = pull_grouped.get("Progression", 0.0)
    pull_grouped["Reclear"] = pull_grouped.get("Reclear", 0.0)

    window_grouped = (
        df.groupby(["raid_date_utc", "classification"])["window_segment_hours"]
        .sum()
        .unstack(fill_value=0)
    )
    window_grouped["Progression"] = window_grouped.get("Progression", 0.0)
    window_grouped["Reclear"] = window_grouped.get("Reclear", 0.0)

    downtime_grouped = (
        df.groupby(["raid_date_utc", "classification"])["downtime_hours"]
        .sum()
        .unstack(fill_value=0)
    )
    downtime_grouped["Progression"] = downtime_grouped.get("Progression", 0.0)
    downtime_grouped["Reclear"] = downtime_grouped.get("Reclear", 0.0)

    pulls_grouped = (
        df.groupby(["raid_date_utc", "classification"])["fight_id"]
        .count()
        .unstack(fill_value=0)
    )
    pulls_grouped["Progression"] = pulls_grouped.get("Progression", 0)
    pulls_grouped["Reclear"] = pulls_grouped.get("Reclear", 0)

    night_summary = pull_grouped.reset_index().rename(
        columns={
            "Progression": "progression_pull_hours",
            "Reclear": "reclear_pull_hours",
        }
    )

    window_summary = window_grouped.reset_index().rename(
        columns={
            "Progression": "progression_window_hours",
            "Reclear": "reclear_window_hours",
        }
    )

    downtime_summary = downtime_grouped.reset_index().rename(
        columns={
            "Progression": "progression_downtime_hours",
            "Reclear": "reclear_downtime_hours",
        }
    )

    pulls_summary = pulls_grouped.reset_index().rename(
        columns={
            "Progression": "progression_pulls",
            "Reclear": "reclear_pulls",
        }
    )

    night_summary = night_summary.merge(window_summary, on="raid_date_utc", how="left")
    night_summary = night_summary.merge(downtime_summary, on="raid_date_utc", how="left")
    night_summary = night_summary.merge(pulls_summary, on="raid_date_utc", how="left")

    night_summary["total_pull_hours"] = night_summary["progression_pull_hours"] + night_summary["reclear_pull_hours"]
    night_summary["total_window_hours"] = night_summary["progression_window_hours"] + night_summary["reclear_window_hours"]
    night_summary["total_downtime_hours"] = night_summary["progression_downtime_hours"] + night_summary["reclear_downtime_hours"]
    night_summary["total_pulls"] = night_summary["progression_pulls"] + night_summary["reclear_pulls"]

    night_summary["progression_pull_percent"] = night_summary.apply(lambda r: safe_percent(r["progression_pull_hours"], r["total_pull_hours"]), axis=1)
    night_summary["reclear_pull_percent"] = night_summary.apply(lambda r: safe_percent(r["reclear_pull_hours"], r["total_pull_hours"]), axis=1)

    night_summary["progression_window_percent"] = night_summary.apply(lambda r: safe_percent(r["progression_window_hours"], r["total_window_hours"]), axis=1)
    night_summary["reclear_window_percent"] = night_summary.apply(lambda r: safe_percent(r["reclear_window_hours"], r["total_window_hours"]), axis=1)

    night_summary["reclear_tax_percent"] = night_summary["reclear_window_percent"]
    night_summary["pull_uptime_percent"] = night_summary.apply(lambda r: safe_percent(r["total_pull_hours"], r["total_window_hours"]), axis=1)
    night_summary["pulls_per_hour"] = night_summary.apply(lambda r: safe_rate(r["total_pulls"], r["total_window_hours"]), axis=1)
    night_summary["progression_pulls_per_hour"] = night_summary.apply(lambda r: safe_rate(r["progression_pulls"], r["progression_window_hours"]), axis=1)
    night_summary["reclear_pulls_per_hour"] = night_summary.apply(lambda r: safe_rate(r["reclear_pulls"], r["reclear_window_hours"]), axis=1)

    raid_day_lookup = {
        date: idx + 1 for idx, date in enumerate(sorted(night_summary["raid_date_utc"].unique()))
    }
    night_summary["raid_day"] = night_summary["raid_date_utc"].map(raid_day_lookup)

    bosses = (
        df.groupby("raid_date_utc")["boss_name"]
        .apply(lambda x: ", ".join(dict.fromkeys(x)))
        .reset_index(name="bosses_pulled")
    )

    kills = (
        df[df["kill"]]
        .groupby("raid_date_utc")["boss_name"]
        .apply(lambda x: ", ".join(dict.fromkeys(x)))
        .reset_index(name="kills")
    )

    night_summary = night_summary.merge(bosses, on="raid_date_utc", how="left")
    night_summary = night_summary.merge(kills, on="raid_date_utc", how="left")
    night_summary["kills"] = night_summary["kills"].fillna("")

    night_summary = night_summary[
        [
            "raid_date_utc",
            "raid_day",
            "total_window_hours",
            "total_pull_hours",
            "total_downtime_hours",
            "pull_uptime_percent",
            "pulls_per_hour",
            "reclear_tax_percent",
            "progression_window_hours",
            "reclear_window_hours",
            "progression_window_percent",
            "reclear_window_percent",
            "progression_downtime_hours",
            "reclear_downtime_hours",
            "progression_pulls",
            "reclear_pulls",
            "progression_pulls_per_hour",
            "reclear_pulls_per_hour",
            "progression_pull_hours",
            "reclear_pull_hours",
            "progression_pull_percent",
            "reclear_pull_percent",
            "bosses_pulled",
            "kills",
        ]
    ].sort_values("raid_date_utc")

    # Overall summary
    total_pull_hours = df["duration_hours"].sum()
    progression_pull_hours = df.loc[df["classification"] == "Progression", "duration_hours"].sum()
    reclear_pull_hours = df.loc[df["classification"] == "Reclear", "duration_hours"].sum()

    total_window_hours = df["window_segment_hours"].sum()
    progression_window_hours = df.loc[df["classification"] == "Progression", "window_segment_hours"].sum()
    reclear_window_hours = df.loc[df["classification"] == "Reclear", "window_segment_hours"].sum()

    total_downtime_hours = df["downtime_hours"].sum()
    progression_downtime_hours = df.loc[df["classification"] == "Progression", "downtime_hours"].sum()
    reclear_downtime_hours = df.loc[df["classification"] == "Reclear", "downtime_hours"].sum()

    total_pulls = len(df)
    progression_pulls = int((df["classification"] == "Progression").sum())
    reclear_pulls = int((df["classification"] == "Reclear").sum())

    reclear_kills = int(((df["classification"] == "Reclear") & df["kill"]).sum())
    reclear_wipes = int(((df["classification"] == "Reclear") & (~df["kill"])).sum())

    overall_summary = pd.DataFrame(
        [
            {
                "raid_days": df["raid_date_utc"].nunique(),
                "total_window_hours": total_window_hours,
                "progression_window_hours": progression_window_hours,
                "reclear_window_hours": reclear_window_hours,
                "reclear_tax_percent": safe_percent(reclear_window_hours, total_window_hours),
                "progression_window_percent": safe_percent(progression_window_hours, total_window_hours),
                "reclear_window_percent": safe_percent(reclear_window_hours, total_window_hours),
                "total_pull_hours": total_pull_hours,
                "progression_pull_hours": progression_pull_hours,
                "reclear_pull_hours": reclear_pull_hours,
                "total_downtime_hours": total_downtime_hours,
                "progression_downtime_hours": progression_downtime_hours,
                "reclear_downtime_hours": reclear_downtime_hours,
                "pull_uptime_percent": safe_percent(total_pull_hours, total_window_hours),
                "total_pulls": total_pulls,
                "progression_pulls": progression_pulls,
                "reclear_pulls": reclear_pulls,
                "pulls_per_hour": safe_rate(total_pulls, total_window_hours),
                "progression_pulls_per_hour": safe_rate(progression_pulls, progression_window_hours),
                "reclear_pulls_per_hour": safe_rate(reclear_pulls, reclear_window_hours),
                "total_kills": int(df["kill"].sum()),
                "reclear_kills": reclear_kills,
                "reclear_wipes": reclear_wipes,
                "reclear_wipe_rate_percent": safe_percent(reclear_wipes, reclear_pulls),
            }
        ]
    )

    # Boss summary
    boss_rows = []
    total_progression_window_for_wall = 0.0
    boss_prog_windows: dict[str, float] = {}

    for boss_name, boss_df in df.groupby("boss_name", sort=False):
        prog_df = boss_df[boss_df["classification"] == "Progression"].sort_values("absolute_start_ms")

        prog_window_seconds = 0.0
        for _, day_df in prog_df.groupby("raid_date_utc"):
            prog_window_seconds += max(day_df["absolute_end_ms"]) / 1000 - min(day_df["absolute_start_ms"]) / 1000

        boss_prog_windows[boss_name] = prog_window_seconds / 3600
        total_progression_window_for_wall += prog_window_seconds / 3600

    for boss_name, boss_df in df.groupby("boss_name", sort=False):
        prog_df = boss_df[boss_df["classification"] == "Progression"].sort_values("absolute_start_ms")
        reclear_df = boss_df[boss_df["classification"] == "Reclear"].sort_values("absolute_start_ms")

        kill_rows = prog_df[prog_df["kill"]].sort_values("absolute_start_ms")
        kill_date = kill_rows["raid_date_utc"].iloc[0] if not kill_rows.empty else ""

        prog_window_hours = boss_prog_windows.get(boss_name, 0.0)

        reclear_window_seconds = 0.0
        for _, day_df in reclear_df.groupby("raid_date_utc"):
            reclear_window_seconds += max(day_df["absolute_end_ms"]) / 1000 - min(day_df["absolute_start_ms"]) / 1000

        reclear_pulls_boss = len(reclear_df)
        reclear_kills_boss = int(reclear_df["kill"].sum())
        reclear_wipes_boss = int((~reclear_df["kill"]).sum())
        total_window_hours_boss = boss_df["window_segment_hours"].sum()
        total_pull_hours_boss = boss_df["duration_hours"].sum()
        downtime_hours_boss = max(0, total_window_hours_boss - total_pull_hours_boss)

        boss_rows.append(
            {
                "boss_name": boss_name,
                "kill_date": kill_date,
                "boss_wall_percent": safe_percent(prog_window_hours, total_progression_window_for_wall),
                "progression_pulls": len(prog_df),
                "reclear_pulls": reclear_pulls_boss,
                "total_pulls": len(boss_df),
                "progression_pull_hours": prog_df["duration_hours"].sum(),
                "reclear_pull_hours": reclear_df["duration_hours"].sum(),
                "total_pull_hours": total_pull_hours_boss,
                "progression_window_hours": prog_window_hours,
                "reclear_window_hours": reclear_window_seconds / 3600,
                "total_window_hours": total_window_hours_boss,
                "downtime_hours": downtime_hours_boss,
                "pull_uptime_percent": safe_percent(total_pull_hours_boss, total_window_hours_boss),
                "progression_pulls_per_hour": safe_rate(len(prog_df), prog_window_hours),
                "reclear_pulls_per_hour": safe_rate(reclear_pulls_boss, reclear_window_seconds / 3600),
                "kills": int(boss_df["kill"].sum()),
                "reclear_kills": reclear_kills_boss,
                "reclear_wipes": reclear_wipes_boss,
                "reclear_wipe_rate_percent": safe_percent(reclear_wipes_boss, reclear_pulls_boss),
            }
        )

    boss_summary = pd.DataFrame(boss_rows).sort_values("boss_wall_percent", ascending=False)

    # Pulls by phase = progression/reclear phase from lightweight fight summaries.
    phase_summary = (
        df.groupby(["boss_name", "phase"])
        .agg(
            pulls=("fight_id", "count"),
            pull_hours=("duration_hours", "sum"),
            window_hours=("window_segment_hours", "sum"),
            downtime_hours=("downtime_hours", "sum"),
            kills=("kill", "sum"),
        )
        .reset_index()
        .sort_values(["boss_name", "phase"])
    )
    phase_summary["pulls_per_hour"] = phase_summary.apply(lambda r: safe_rate(r["pulls"], r["window_hours"]), axis=1)
    phase_summary["pull_uptime_percent"] = phase_summary.apply(lambda r: safe_percent(r["pull_hours"], r["window_hours"]), axis=1)

    # New efficiency summary sheet.
    efficiency_summary = pd.DataFrame(
        [
            {
                "metric": "Reclear tax",
                "value": safe_percent(reclear_window_hours, total_window_hours),
                "unit": "%",
                "meaning": "Share of total window time spent on reclear.",
            },
            {
                "metric": "Total downtime",
                "value": total_downtime_hours,
                "unit": "hours",
                "meaning": "Window time minus boss pull duration.",
            },
            {
                "metric": "Pull uptime",
                "value": safe_percent(total_pull_hours, total_window_hours),
                "unit": "%",
                "meaning": "Boss pull duration as a share of window time.",
            },
            {
                "metric": "Pulls per hour",
                "value": safe_rate(total_pulls, total_window_hours),
                "unit": "pulls/hour",
                "meaning": "Total pulls divided by total window hours.",
            },
            {
                "metric": "Reclear wipes",
                "value": reclear_wipes,
                "unit": "pulls",
                "meaning": "Reclear pulls that were not kills.",
            },
        ]
    )

    return night_summary, overall_summary, boss_summary, phase_summary, efficiency_summary


def export_outputs(
    fight_rows: list[dict],
    output_folder: str = "output",
    selected_reports: list[dict] | None = None,
    warnings: list[dict] | None = None,
    report_audit: list[dict] | None = None,
    report_boss_audit: list[dict] | None = None,
    clear_output_folder: bool = True,
) -> None:
    output_path = Path(output_folder)

    if clear_output_folder and output_path.exists():
        for old_file in output_path.iterdir():
            if old_file.is_file() and old_file.suffix.lower() in {".csv", ".xlsx", ".txt", ".log"}:
                old_file.unlink()

    output_path.mkdir(exist_ok=True)

    fight_df = pd.DataFrame(fight_rows)

    if not fight_df.empty:
        ordered_columns = [
            "raid_date_utc",
            "classification",
            "phase",
            "boss_name",
            "kill",
            "duration_minutes",
            "duration_hours",
            "window_segment_minutes",
            "window_segment_hours",
            "report_code",
            "report_title",
            "fight_id",
            "encounter_id",
            "difficulty",
            "start_ms",
            "end_ms",
            "absolute_start_ms",
            "absolute_end_ms",
        ]

        fight_df = fight_df[ordered_columns]
        fight_df["downtime_hours"] = (fight_df["window_segment_hours"] - fight_df["duration_hours"]).clip(lower=0)
        fight_df["downtime_minutes"] = fight_df["downtime_hours"] * 60

        for col in [
            "duration_minutes",
            "duration_hours",
            "window_segment_minutes",
            "window_segment_hours",
            "downtime_minutes",
            "downtime_hours",
        ]:
            fight_df[col] = fight_df[col].round(3)

    night_summary, overall_summary, boss_summary, phase_summary, efficiency_summary = build_summaries(fight_df)

    for df in [night_summary, overall_summary, boss_summary, phase_summary, efficiency_summary]:
        for col in df.columns:
            if "hours" in col or "percent" in col or "per_hour" in col or col == "value":
                if pd.api.types.is_numeric_dtype(df[col]):
                    df[col] = df[col].round(2)

    fight_df.to_csv(output_path / "fight_breakdown.csv", index=False)
    night_summary.to_csv(output_path / "night_summary.csv", index=False)
    overall_summary.to_csv(output_path / "overall_summary.csv", index=False)
    boss_summary.to_csv(output_path / "boss_summary.csv", index=False)
    phase_summary.to_csv(output_path / "pulls_by_phase.csv", index=False)
    efficiency_summary.to_csv(output_path / "efficiency_summary.csv", index=False)

    selected_reports_df = pd.DataFrame(selected_reports or [])
    if not selected_reports_df.empty:
        selected_reports_df.to_csv(output_path / "selected_reports.csv", index=False)

    warnings_df = pd.DataFrame(warnings or [])
    if not warnings_df.empty:
        warnings_df.to_csv(output_path / "warnings.csv", index=False)

    report_audit_df = pd.DataFrame(report_audit or [])
    if not report_audit_df.empty:
        report_audit_df.to_csv(output_path / "report_audit.csv", index=False)

    report_boss_audit_df = pd.DataFrame(report_boss_audit or [])
    if not report_boss_audit_df.empty:
        report_boss_audit_df.to_csv(output_path / "report_boss_audit.csv", index=False)

    excel_path = output_path / "wcl_reclear_tracker.xlsx"
    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        overall_summary.to_excel(writer, sheet_name="Overall Summary", index=False)
        efficiency_summary.to_excel(writer, sheet_name="Efficiency Summary", index=False)
        night_summary.to_excel(writer, sheet_name="Night Summary", index=False)
        boss_summary.to_excel(writer, sheet_name="Boss Summary", index=False)
        phase_summary.to_excel(writer, sheet_name="Pulls By Phase", index=False)
        fight_df.to_excel(writer, sheet_name="Fight Breakdown", index=False)
        if not selected_reports_df.empty:
            selected_reports_df.to_excel(writer, sheet_name="Selected Reports", index=False)
        if not warnings_df.empty:
            warnings_df.to_excel(writer, sheet_name="Warnings", index=False)
        if not report_audit_df.empty:
            report_audit_df.to_excel(writer, sheet_name="Report Audit", index=False)
        if not report_boss_audit_df.empty:
            report_boss_audit_df.to_excel(writer, sheet_name="Report Boss Audit", index=False)

    print(f"Exported: {excel_path}")
