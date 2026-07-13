from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, quote
import csv
import html
import json
import re
import time

import requests

from schedule_database import connect_schedule_db, get_cached_discovered_guilds, upsert_discovery_cache


JsonDict = dict[str, Any]


@dataclass(slots=True)
class DiscoveredGuild:
    rank: int
    guild: str
    realm: str
    region: str
    source: str
    url: str = ""
    endboss_kill_timestamp_ms: int | None = None
    endboss_kill_date: str = ""
    endboss_kill_source: str = ""


def guild_from_dict(row: dict[str, Any]) -> DiscoveredGuild:
    return DiscoveredGuild(
        rank=int(row.get("rank") or 0),
        guild=str(row.get("guild") or ""),
        realm=str(row.get("realm") or ""),
        region=str(row.get("region") or "").upper(),
        source=str(row.get("source") or "raiderio_discovery_cache"),
        url=str(row.get("url") or ""),
        endboss_kill_timestamp_ms=int(row["endboss_kill_timestamp_ms"]) if row.get("endboss_kill_timestamp_ms") not in (None, "") else None,
        endboss_kill_date=str(row.get("endboss_kill_date") or ""),
        endboss_kill_source=str(row.get("endboss_kill_source") or ""),
    )


def normalise(value: str) -> str:
    return re.sub(r"\s+", " ", str(value).strip()).lower()


def coerce_timestamp_ms(value: Any) -> int | None:
    """
    Accepts epoch seconds/ms or ISO-ish datetime strings and returns epoch ms.
    This is deliberately tolerant because Raider.IO payload shapes can change.
    """
    if value is None or value == "":
        return None

    try:
        # Important: bool is a subclass of int in Python.
        # Raider.IO uses fields such as isDefeated=true; that must not become timestamp 1 / 1970-01-01.
        if isinstance(value, bool):
            return None

        if isinstance(value, (int, float)):
            n = int(value)
            if n <= 0:
                return None
            # Epoch seconds vs epoch milliseconds.
            return n * 1000 if n < 10_000_000_000 else n
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            if re.fullmatch(r"\d+", text):
                return coerce_timestamp_ms(int(text))
            # Basic ISO datetime support without adding dateutil dependency.
            from datetime import datetime, timezone
            iso = text.replace("Z", "+00:00")
            dt = datetime.fromisoformat(iso)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
    except Exception:
        return None

    return None


def date_from_timestamp_ms(value: int | None) -> str:
    if not value:
        return ""
    from datetime import datetime, timezone
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).date().isoformat()


def find_endboss_kill_timestamp(obj: Any, endboss_slug_or_name: str = "midnight-falls") -> tuple[int | None, str]:
    """
    Best-effort extraction of the final boss kill timestamp from Raider.IO ranking payloads.

    We look for common keys near objects mentioning Midnight Falls/Lura. If no nested
    boss object is obvious, we also accept top-level completion timestamps.
    """
    boss_terms = [
        "midnight-falls",
        "midnight falls",
        "lura",
        str(endboss_slug_or_name or "").strip().lower(),
    ]
    timestamp_keys = {
        "defeated_at", "defeatedAt", "first_defeated", "firstDefeated",
        "killed_at", "killedAt", "kill_date", "killDate",
        "completed_at", "completedAt", "clear_time", "clearTime",
        "clear_time_ms", "clearTimeMs", "timestamp", "time"
    }
    # Keys that are status flags, not dates.
    blocked_timestamp_keys = {
        "isDefeated", "is_defeated", "defeated", "killed", "complete", "completed", "cleared"
    }

    def node_mentions_boss(node: Any) -> bool:
        if isinstance(node, dict):
            for key in ("slug", "name", "encounter", "boss", "id"):
                value = node.get(key)
                if isinstance(value, str):
                    low = value.lower()
                    if any(term and term in low for term in boss_terms):
                        return True
            # Also check shallow string values; payload shapes vary.
            for value in node.values():
                if isinstance(value, str):
                    low = value.lower()
                    if any(term and term in low for term in boss_terms):
                        return True
        return False

    def timestamps_in_node(node: dict[str, Any]) -> list[tuple[int, str]]:
        out: list[tuple[int, str]] = []
        for key, value in node.items():
            if key in blocked_timestamp_keys:
                continue
            key_lower = key.lower()

            # Accept explicit date/time-ish fields. Do not accept generic boolean status fields.
            is_timestamp_key = (
                key in timestamp_keys
                or "date" in key_lower
                or "time" in key_lower
                or key_lower.endswith("_at")
                or key_lower.endswith("at")
                or key in {"firstDefeated", "first_defeated"}
            )

            if is_timestamp_key:
                ts = coerce_timestamp_ms(value)
                if ts:
                    # Reject impossible season dates. This prevents accidental 1970 cutoffs.
                    if ts < 1_700_000_000_000:
                        continue
                    out.append((ts, key))
        return out

    found: list[tuple[int, str]] = []

    def walk(node: Any, boss_context: bool = False) -> None:
        current_context = boss_context or node_mentions_boss(node)
        if isinstance(node, dict):
            if current_context:
                found.extend(timestamps_in_node(node))
            for value in node.values():
                walk(value, current_context)
        elif isinstance(node, list):
            for item in node:
                walk(item, current_context)

    walk(obj)

    if found:
        found.sort(key=lambda x: x[0])
        ts, source = found[0]
        return ts, f"raiderio:{source}:boss_context"

    # Fallback: whole ranking row may expose a final clear/completion time.
    if isinstance(obj, dict):
        fallback = timestamps_in_node(obj)
        if fallback:
            fallback.sort(key=lambda x: x[0])
            ts, source = fallback[0]
            return ts, f"raiderio:{source}:row_fallback"

    return None, ""


def write_debug(path: str | Path, lines: list[str]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def fetch_json(url: str, params: dict[str, Any]) -> JsonDict:
    headers = {
        "User-Agent": "WCLReclearTracker/1.5.7 guild-discovery; local personal tool",
        "Accept": "application/json,text/plain,*/*",
    }
    response = requests.get(url, params=params, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()


def fetch_text(url: str, params: dict[str, Any] | None = None) -> str:
    headers = {
        "User-Agent": "WCLReclearTracker/1.5.7 guild-discovery; local personal tool",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    response = requests.get(url, params=params or {}, headers=headers, timeout=30)
    response.raise_for_status()
    return response.text


def extract_text_from_html(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", value)
    return html.unescape(value).strip()


def extract_guild_from_object(obj: Any, default_region: str, source: str, fallback_rank: int) -> DiscoveredGuild | None:
    if not isinstance(obj, dict):
        return None

    rank_raw = (
        obj.get("rank")
        or obj.get("world_rank")
        or obj.get("region_rank")
        or obj.get("ranked_at")
        or fallback_rank
    )
    try:
        rank = int(rank_raw)
    except Exception:
        rank = fallback_rank

    candidates = []
    if isinstance(obj.get("guild"), dict):
        candidates.append(obj.get("guild"))
    if isinstance(obj.get("guild"), str):
        candidates.append({"name": obj.get("guild")})
    candidates.append(obj)

    for guild_obj in candidates:
        if not isinstance(guild_obj, dict):
            continue

        guild = (
            guild_obj.get("name")
            or guild_obj.get("guild_name")
            or guild_obj.get("guild")
            or obj.get("guild_name")
            or obj.get("name")
            or ""
        )

        if isinstance(guild, dict):
            guild = guild.get("name", "")

        realm = ""
        realm_obj = (
            guild_obj.get("realm")
            or guild_obj.get("server")
            or obj.get("realm")
            or obj.get("server")
            or obj.get("realm_name")
        )
        if isinstance(realm_obj, dict):
            realm = (
                realm_obj.get("name")
                or realm_obj.get("slug")
                or realm_obj.get("realm")
                or realm_obj.get("connected_realm")
                or ""
            )
        elif isinstance(realm_obj, str):
            realm = realm_obj

        region = ""
        region_obj = guild_obj.get("region") or obj.get("region")
        if isinstance(region_obj, dict):
            region = region_obj.get("slug") or region_obj.get("name") or region_obj.get("region") or ""
        elif isinstance(region_obj, str):
            region = region_obj

        region = (region or default_region).upper()

        profile_url = (
            guild_obj.get("profile_url")
            or guild_obj.get("url")
            or obj.get("profile_url")
            or obj.get("url")
            or ""
        )

        if guild and realm:
            kill_ts, kill_source = find_endboss_kill_timestamp(obj)
            return DiscoveredGuild(
                rank=rank,
                guild=str(guild).strip(),
                realm=str(realm).strip(),
                region=region,
                source=source,
                url=str(profile_url).strip(),
                endboss_kill_timestamp_ms=kill_ts,
                endboss_kill_date=date_from_timestamp_ms(kill_ts),
                endboss_kill_source=kill_source,
            )

    return None


def walk_json_for_guilds(data: Any, default_region: str, source: str) -> list[DiscoveredGuild]:
    found: list[DiscoveredGuild] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            parsed = extract_guild_from_object(node, default_region, source, len(found) + 1)
            if parsed:
                found.append(parsed)

            for value in node.values():
                walk(value)

        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data)
    return dedupe_guilds(found)


def dedupe_guilds(guilds: list[DiscoveredGuild]) -> list[DiscoveredGuild]:
    deduped: list[DiscoveredGuild] = []
    seen: set[tuple[str, str, str]] = set()

    for item in guilds:
        if not item.guild or not item.realm:
            continue
        key = (normalise(item.guild), normalise(item.realm), item.region.upper())
        if key in seen:
            continue
        seen.add(key)
        item.rank = len(deduped) + 1
        deduped.append(item)

    return deduped


def raiderio_api_attempts(discovery: JsonDict, page: int) -> list[tuple[str, dict[str, Any]]]:
    raid_slug = discovery.get("raid_slug", "tier-mn-1")
    difficulty = discovery.get("difficulty", "mythic")
    region = (discovery.get("region", "eu") or "eu").lower()
    limit = int(discovery.get("api_limit", 200))

    url = "https://raider.io/api/v1/raiding/raid-rankings"

    return [
        (url, {"raid": raid_slug, "difficulty": difficulty, "region": region, "page": page, "limit": limit}),
        (url, {"raid": raid_slug, "difficulty": difficulty, "region": region.upper(), "page": page, "limit": limit}),
        (url, {"raid": raid_slug, "difficulty": difficulty, "region": region, "page": page}),
        (url, {"raid": raid_slug, "difficulty": difficulty, "region": region, "limit": limit}),
        (url, {"raid": raid_slug, "difficulty": difficulty, "region": region.upper(), "limit": limit}),
        (url, {"name": raid_slug, "difficulty": difficulty, "region": region, "limit": limit}),
        (url, {"slug": raid_slug, "difficulty": difficulty, "region": region, "limit": limit}),
    ]


def discover_raiderio_api_guilds(config: JsonDict, logger=None) -> tuple[list[DiscoveredGuild], list[str]]:
    discovery = config.get("comparison", {}).get("discovery", {})
    region = (discovery.get("region", "eu") or "eu").lower()
    raid_slug = discovery.get("raid_slug", "tier-mn-1")
    max_pages = int(discovery.get("max_pages", 10))
    page_start = int(discovery.get("page_start", 0))
    max_rank = int(discovery.get("max_rank_to_fetch", 2000))
    delay = float(discovery.get("delay_between_pages_seconds", 0.75))

    debug: list[str] = [f"raid_slug={raid_slug}", "expected_midnight_slug=tier-mn-1", f"max_rank_to_fetch={max_rank}"]
    all_found: list[DiscoveredGuild] = []
    seen: set[tuple[str, str, str]] = set()

    for page in range(page_start, page_start + max_pages):
        page_found = 0

        for url, params in raiderio_api_attempts(discovery, page):
            full_url = f"{url}?{urlencode(params)}"
            if logger:
                logger.print(f"Discovering Raider.IO guilds: page {page}")

            try:
                data = fetch_json(url, params)
            except Exception as e:
                debug.append(f"FAIL {full_url} :: {type(e).__name__}: {e}")
                continue

            parsed = walk_json_for_guilds(data, default_region=region.upper(), source="raiderio_raid_rankings_api")
            debug.append(f"OK {full_url} :: parsed={len(parsed)}")

            if not parsed:
                continue

            for item in parsed:
                if item.region.lower() != region.lower():
                    continue
                key = (normalise(item.guild), normalise(item.realm), item.region.upper())
                if key in seen:
                    continue
                seen.add(key)
                item.rank = len(all_found) + 1
                if len(all_found) >= max_rank:
                    break
                all_found.append(item)
                page_found += 1

            # If an API shape worked for this page, don't try the rest of the shapes for the same page.
            break

        if len(all_found) >= max_rank:
            debug.append(f"STOP max_rank reached: {max_rank}")
            break

        if page_found == 0 and page > page_start:
            debug.append(f"STOP page={page}: no new guilds found")
            break

        if delay:
            time.sleep(delay)

    debug.append(f"TOTAL raiderio_api_unique={len(all_found)}")
    return all_found[:max_rank], debug


def html_urls(discovery: JsonDict, page: int) -> list[str]:
    raid_slug = discovery.get("raid_slug", "tier-mn-1")
    difficulty = discovery.get("difficulty", "mythic")
    region = (discovery.get("region", "eu") or "eu").lower()

    return [
        f"https://raider.io/raids/{quote(raid_slug)}/rankings/{region}/{difficulty}?page={page}",
        f"https://raider.io/raids/{quote(raid_slug)}/rankings/world/{difficulty}?region={region}&page={page}",
        f"https://raider.io/raids/{quote(raid_slug)}/rankings?region={region}&difficulty={difficulty}&page={page}",
    ]


def parse_raiderio_html(text: str, region: str) -> list[DiscoveredGuild]:
    found: list[DiscoveredGuild] = []

    # Look for guild profile links:
    # /guilds/eu/draenor/Guild%20Name
    link_pattern = re.compile(
        r'href="(?P<href>/guilds/(?P<region>[a-z]+)/(?P<realm>[^/"]+)/(?P<guild>[^"]+))"',
        re.I,
    )

    for match in link_pattern.finditer(text):
        href = html.unescape(match.group("href"))
        guild_slug = html.unescape(match.group("guild")).replace("%20", " ")
        guild_slug = guild_slug.replace("+", " ")
        guild = re.sub(r"[-_]+", " ", guild_slug).strip()
        realm = html.unescape(match.group("realm")).replace("-", " ").strip()
        item_region = match.group("region").upper()

        if item_region.lower() != region.lower():
            continue

        found.append(
            DiscoveredGuild(
                rank=len(found) + 1,
                guild=guild,
                realm=realm,
                region=item_region,
                source="raiderio_html_guild_link",
                url="https://raider.io" + href,
            )
        )

    return dedupe_guilds(found)


def discover_raiderio_html_guilds(config: JsonDict, logger=None) -> tuple[list[DiscoveredGuild], list[str]]:
    discovery = config.get("comparison", {}).get("discovery", {})
    region = (discovery.get("region", "eu") or "eu").lower()
    raid_slug = discovery.get("raid_slug", "tier-mn-1")
    max_pages = int(discovery.get("max_pages", 10))
    page_start = int(discovery.get("page_start", 0))
    max_rank = int(discovery.get("max_rank_to_fetch", 2000))
    delay = float(discovery.get("delay_between_pages_seconds", 0.75))

    debug: list[str] = [f"raid_slug={raid_slug}", "expected_midnight_slug=tier-mn-1", f"max_rank_to_fetch={max_rank}"]
    all_found: list[DiscoveredGuild] = []
    seen: set[tuple[str, str, str]] = set()

    for page in range(page_start, page_start + max_pages):
        page_found = 0
        for url in html_urls(discovery, page):
            try:
                text = fetch_text(url)
            except Exception as e:
                debug.append(f"FAIL HTML {url} :: {type(e).__name__}: {e}")
                continue

            parsed = parse_raiderio_html(text, region)
            debug.append(f"OK HTML {url} :: parsed={len(parsed)}")

            if not parsed:
                continue

            for item in parsed:
                key = (normalise(item.guild), normalise(item.realm), item.region.upper())
                if key in seen:
                    continue
                seen.add(key)
                item.rank = len(all_found) + 1
                if len(all_found) >= max_rank:
                    break
                all_found.append(item)
                page_found += 1

            break

        if page_found == 0 and page > page_start:
            debug.append(f"STOP HTML page={page}: no new guilds found")
            break

        if delay:
            time.sleep(delay)

    debug.append(f"TOTAL raiderio_html_unique={len(all_found)}")
    return all_found, debug


def discover_guilds(config: JsonDict, logger=None) -> list[DiscoveredGuild]:
    discovery = config.get("comparison", {}).get("discovery", {})
    debug_path = discovery.get("debug_output_file", "output/comparison/discovery_debug.txt")

    raid_slug = discovery.get("raid_slug", "tier-mn-1")
    difficulty = discovery.get("difficulty", "mythic")
    region = (discovery.get("region", "eu") or "eu").lower()
    max_rank = int(discovery.get("max_rank_to_fetch", 2000))
    cache_enabled = bool(discovery.get("cache_enabled", True))
    ttl_hours = float(discovery.get("cache_ttl_hours", 168))
    force_refresh = bool(discovery.get("force_refresh_cache", False))
    cache_version = str(discovery.get("cache_schema_version", "v1"))

    debug: list[str] = []

    if cache_enabled and not force_refresh:
        try:
            conn = connect_schedule_db(config)
            cached = get_cached_discovered_guilds(conn, raid_slug, difficulty, region, max_rank, ttl_hours, cache_version=cache_version)
            conn.close()
            if cached is not None:
                found = [guild_from_dict(row) for row in cached]
                debug.append(f"DISCOVERY CACHE HIT sqlite top{max_rank}: {len(found)} guilds")
                write_debug(debug_path, debug)
                if logger:
                    logger.print(f"Raider.IO discovery cache hit: {len(found)} guilds")
                return found
        except Exception as e:
            debug.append(f"DISCOVERY CACHE READ FAILED: {type(e).__name__}: {e}")

    found, api_debug = discover_raiderio_api_guilds(config, logger=logger)
    debug.extend(api_debug)

    if not found and discovery.get("try_html_fallback", True):
        if logger:
            logger.print("Raider.IO API discovery found no guilds. Trying Raider.IO HTML fallback.")
        html_found, html_debug = discover_raiderio_html_guilds(config, logger=logger)
        found = html_found[:max_rank]
        debug.extend(html_debug)

    if cache_enabled and found:
        try:
            conn = connect_schedule_db(config)
            upsert_discovery_cache(conn, raid_slug, difficulty, region, max_rank, found, cache_version=cache_version)
            conn.close()
            debug.append(f"DISCOVERY CACHE WRITE sqlite top{max_rank}: {len(found)} guilds")
        except Exception as e:
            debug.append(f"DISCOVERY CACHE WRITE FAILED: {type(e).__name__}: {e}")

    write_debug(debug_path, debug)
    return found[:max_rank]


def select_guilds_around_own(
    guilds: list[DiscoveredGuild],
    own_guild: str,
    own_realm: str,
    own_region: str,
    above: int,
    below: int,
    max_used: int,
) -> list[DiscoveredGuild]:
    if not guilds:
        return []

    own_key = (normalise(own_guild), normalise(own_realm), own_region.strip().upper())

    own_index: int | None = None
    for idx, item in enumerate(guilds):
        item_key = (normalise(item.guild), normalise(item.realm), item.region.upper())
        if item_key == own_key:
            own_index = idx
            break

    if own_index is None:
        return guilds[:max_used]

    start = max(0, own_index - above)
    end = min(len(guilds), own_index + below + 1)
    selected = guilds[start:end]

    if len(selected) > max_used:
        selected = selected[-max_used:]

    return selected


def write_discovered_guilds(path: str | Path, guilds: list[DiscoveredGuild]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)

    with output.open("w", encoding="utf-8", newline="") as f:
        fieldnames = ["rank", "guild", "realm", "region", "source", "url", "endboss_kill_timestamp_ms", "endboss_kill_date", "endboss_kill_source"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for item in guilds:
            writer.writerow(asdict(item))


def discovered_to_comparison_rows(guilds: list[DiscoveredGuild]) -> list[JsonDict]:
    rows: list[JsonDict] = []
    for item in guilds:
        if not item.guild or not item.realm:
            continue
        rows.append(
            {
                "guild": item.guild,
                "realm": item.realm,
                "region": item.region.upper(),
                "declared_days_per_week": None,
                "declared_hours_per_week": None,
                "schedule_source": None,
            }
        )
    return rows
