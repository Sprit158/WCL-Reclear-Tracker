from __future__ import annotations

from dataclasses import dataclass, asdict
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, unquote, quote_plus
import csv
import re
import time

import requests


type JsonDict = dict[str, Any]


@dataclass(slots=True)
class WowProgressGuild:
    rank: int | None
    guild: str
    region: str
    realm: str
    progress: str
    raids_week: str
    source: str
    profile_url: str


def clean_text(value: str) -> str:
    value = re.sub(r"<script.*?</script>", "", value, flags=re.I | re.S)
    value = re.sub(r"<style.*?</style>", "", value, flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    value = unescape(value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def decode_slug(value: str) -> str:
    value = unquote(value)
    value = value.replace("+", " ")
    return value.strip()


def fetch_page(url: str, params: dict[str, Any]) -> str:
    headers = {
        "User-Agent": "WCLReclearTracker/1.6.16 wowprogress-test; local personal tool",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    response = requests.get(url, params=params, headers=headers, timeout=45)
    response.raise_for_status()
    return response.text


def page_params(config: JsonDict, page: int) -> dict[str, Any]:
    wp = config.get("comparison", {}).get("wowprogress", {})
    params = {
        "faction": "",
        "raids_week": wp.get("raids_week", "1-2"),
        "lang": "",
        "class": "",
        "spec": "",
    }
    # WoWProgress historically uses page=N on listing pages. The first page works with no page param.
    if page > 1:
        params["page"] = page
    return params


def parse_wowprogress_guilds(html: str, raids_week: str) -> list[WowProgressGuild]:
    """
    Parse WoWProgress guild rows.

    The page is old-style HTML and can vary, so this uses a tolerant link-based parser:
      1. find /guild/<region>/<realm>/<guild> links
      2. inspect text shortly before/after each link for rank/progress
    """
    rows: list[WowProgressGuild] = []
    seen: set[tuple[str, str, str]] = set()

    # Match href='/guild/eu/tarren-mill/this+is+fine' or double quoted equivalents.
    link_re = re.compile(
        r'<a[^>]+href=["\'](?P<href>/guild/(?P<region>[^/"\']+)/(?P<realm>[^/"\']+)/(?P<guild>[^"\']+))["\'][^>]*>(?P<label>.*?)</a>',
        re.I | re.S,
    )

    matches = list(link_re.finditer(html))
    for match in matches:
        href = unescape(match.group("href"))
        region = decode_slug(match.group("region")).upper()
        realm = decode_slug(match.group("realm"))
        guild_from_url = decode_slug(match.group("guild"))
        label = clean_text(match.group("label"))
        guild = label or guild_from_url

        if not guild or not realm or not region:
            continue

        key = (region.lower(), realm.lower(), guild.lower())
        if key in seen:
            continue
        seen.add(key)

        # Look around the guild link. In rendered text the row is normally:
        # rank guild realm progress
        start = max(0, match.start() - 600)
        end = min(len(html), match.end() + 900)
        context_html = html[start:end]
        context_text = clean_text(context_html)

        rank: int | None = None
        progress = ""

        # Prefer the closest integer before the guild name.
        before_text = clean_text(html[start:match.start()])
        rank_matches = re.findall(r"\b(\d{1,5})\b", before_text)
        if rank_matches:
            try:
                rank = int(rank_matches[-1])
            except Exception:
                rank = None

        # Progress generally looks like 6/6 (M) 1/1 (M) 2/2 (M)
        prog_match = re.search(r"(\d+/\d+\s*\([A-Z]\)(?:\s+\d+/\d+\s*\([A-Z]\))*)", context_text)
        if prog_match:
            progress = prog_match.group(1).strip()

        rows.append(
            WowProgressGuild(
                rank=rank,
                guild=guild,
                region=region,
                realm=realm,
                progress=progress,
                raids_week=raids_week,
                source="wowprogress_raids_week_filter",
                profile_url="https://www.wowprogress.com" + href,
            )
        )

    # The link parser can pick up duplicate sidebar/tooltip links; keep rows with progress first.
    rows.sort(key=lambda r: (r.rank is None, r.rank or 999999, r.guild.lower()))
    return rows


def write_rows(path: str | Path, rows: list[WowProgressGuild]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = ["rank", "guild", "region", "realm", "progress", "raids_week", "source", "profile_url"]
    with output.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def run_wowprogress_test(config: JsonDict, logger=None) -> None:
    wp = config.get("comparison", {}).get("wowprogress", {})
    base_url = wp.get("base_url", "https://www.wowprogress.com/")
    raids_week = wp.get("raids_week", "1-2")
    region_filter = str(wp.get("region_filter", "EU")).upper()
    max_pages = int(wp.get("max_pages", 3))
    delay = float(wp.get("delay_between_pages_seconds", 1.0))
    output_file = wp.get("output_file", "output/comparison/wowprogress_1_2_raids.csv")
    debug_file = wp.get("debug_file", "output/comparison/wowprogress_debug.txt")

    debug: list[str] = []
    all_rows: list[WowProgressGuild] = []
    seen: set[tuple[str, str, str]] = set()

    for page in range(1, max_pages + 1):
        params = page_params(config, page)
        if logger:
            logger.print(f"Fetching WoWProgress 1-2 raids/week page {page}")

        try:
            html = fetch_page(base_url, params)
        except Exception as e:
            debug.append(f"FAIL page={page} params={params} :: {type(e).__name__}: {e}")
            continue

        rows = parse_wowprogress_guilds(html, raids_week=raids_week)
        debug.append(f"OK page={page} params={params} parsed={len(rows)}")

        kept = 0
        for row in rows:
            if region_filter and row.region.upper() != region_filter:
                continue
            key = (row.region.lower(), row.realm.lower(), row.guild.lower())
            if key in seen:
                continue
            seen.add(key)
            all_rows.append(row)
            kept += 1

        debug.append(f"KEEP page={page} region={region_filter} kept={kept}")

        if delay and page < max_pages:
            time.sleep(delay)

    write_rows(output_file, all_rows)
    Path(debug_file).parent.mkdir(parents=True, exist_ok=True)
    Path(debug_file).write_text("\n".join(debug) + "\n", encoding="utf-8")

    if logger:
        logger.print(f"WoWProgress output: {output_file}")
        logger.print(f"WoWProgress debug: {debug_file}")
        logger.print(f"WoWProgress EU 1-2 raids/week guilds parsed: {len(all_rows)}")
