from __future__ import annotations

from pathlib import Path
from typing import Any
import csv
import re

import pandas as pd

from api.wcl_api_v2 import WCLV2ApiError
from guild_fetcher import load_or_fetch_guild_reports, season_range_ms
from settings_manager import get_guild_profile_from_settings
from v2_setup import build_v2_client, save_client_token


JsonDict = dict[str, Any]


def safe_zone_id(report: JsonDict) -> int | str:
    zone = report.get("zone")
    if isinstance(zone, dict):
        return zone.get("id", "") or report.get("zoneID", "")
    return report.get("zoneID", zone or "")


def safe_zone_name(report: JsonDict) -> str:
    zone = report.get("zone")
    if isinstance(zone, dict):
        return str(zone.get("name", "") or report.get("zoneName", ""))
    return str(report.get("zoneName", ""))


def safe_report_start(report: JsonDict) -> int | str:
    return report.get("start") or report.get("startTime") or ""


def slugify_realm(realm: str) -> str:
    value = realm.strip().lower()
    value = value.replace("'", "")
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value


def v2_reports_to_v1_meta(reports: list[JsonDict]) -> list[JsonDict]:
    converted: list[JsonDict] = []

    for report in reports:
        zone = report.get("zone") or {}
        converted.append(
            {
                "code": report.get("code"),
                "id": report.get("code"),
                "title": report.get("title", ""),
                "start": int(report.get("startTime", 0) or 0),
                "end": int(report.get("endTime", 0) or 0),
                "zone": zone,
                "zoneID": zone.get("id"),
                "zoneName": zone.get("name"),
                "source": "v2",
            }
        )

    return [r for r in converted if r.get("code")]


def fetch_guild_reports_v2(config: JsonDict, guild: str, realm: str, region: str) -> list[JsonDict]:
    season_config = config.get("season", {})
    start_ms, end_ms = season_range_ms(season_config)

    client = build_v2_client(config)
    reports = client.fetch_guild_reports(
        guild_name=guild,
        guild_server_slug=slugify_realm(realm),
        guild_server_region=region.upper(),
        start_time=start_ms,
        end_time=end_ms,
        max_pages=20,
    )
    save_client_token(client)

    return v2_reports_to_v1_meta(reports)


def write_report_list(path: Path, reports: list[JsonDict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for report in reports:
        rows.append(
            {
                "code": report.get("code"),
                "title": report.get("title"),
                "start": report.get("start"),
                "end": report.get("end"),
                "zone_id": safe_zone_id(report),
                "zone_name": safe_zone_name(report),
                "source": report.get("source", ""),
            }
        )
    pd.DataFrame(rows).to_csv(path, index=False)


def write_v1_v2_audit(path: Path, v1_reports: list[JsonDict], v2_reports: list[JsonDict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    v1_by_code = {r.get("code") or r.get("id"): r for r in v1_reports}
    v2_by_code = {r.get("code") or r.get("id"): r for r in v2_reports}
    all_codes = sorted(set(v1_by_code) | set(v2_by_code))

    rows = []
    for code in all_codes:
        v1 = v1_by_code.get(code)
        v2 = v2_by_code.get(code)
        source_status = (
            "both" if v1 and v2 else
            "v1_only" if v1 else
            "v2_only"
        )
        report = v2 or v1 or {}
        rows.append(
            {
                "report_code": code,
                "status": source_status,
                "title": report.get("title", ""),
                "v1_found": bool(v1),
                "v2_found": bool(v2),
                "v1_start": safe_report_start(v1) if v1 else "",
                "v2_start": safe_report_start(v2) if v2 else "",
                "v1_zone_id": safe_zone_id(v1) if v1 else "",
                "v2_zone_id": safe_zone_id(v2) if v2 else "",
                "v1_zone": safe_zone_name(v1) if v1 else "",
                "v2_zone": safe_zone_name(v2) if v2 else "",
            }
        )

    pd.DataFrame(rows).to_csv(path, index=False)


def run_v2_report_test(config: JsonDict, v1_client, logger) -> None:
    profile = get_guild_profile_from_settings()

    if not profile:
        logger.print("No saved guild profile found. Run the normal tracker once and save your guild.")
        return

    guild = profile.name
    realm = profile.realm
    region = profile.region

    season_config = config.get("season", {})
    start_ms, end_ms = season_range_ms(season_config)

    logger.print(f"Testing v2 guild report discovery for {guild}-{realm}-{region}")

    try:
        v2_reports = fetch_guild_reports_v2(config, guild, realm, region)
    except WCLV2ApiError as e:
        logger.print(f"v2 report discovery failed: {e}")
        return
    except Exception as e:
        logger.print(f"v2 report discovery failed unexpectedly: {e}")
        return

    try:
        v1_reports, v1_source = load_or_fetch_guild_reports(
            client=v1_client,
            guild_name=guild,
            realm=realm,
            region=region,
            start_ms=start_ms,
            end_ms=end_ms,
            force_refresh=True,
        )
    except Exception as e:
        logger.print(f"v1 report discovery failed: {e}")
        v1_reports = []
        v1_source = "failed"

    for report in v1_reports:
        report["source"] = "v1"

    output = Path("output")
    write_report_list(output / "v2_report_test.csv", v2_reports)
    write_v1_v2_audit(output / "v1_vs_v2_report_audit.csv", v1_reports, v2_reports)

    logger.print(f"v1 reports found: {len(v1_reports)} [{v1_source}]")
    logger.print(f"v2 reports found: {len(v2_reports)}")
    logger.print("Wrote output/v2_report_test.csv")
    logger.print("Wrote output/v1_vs_v2_report_audit.csv")
