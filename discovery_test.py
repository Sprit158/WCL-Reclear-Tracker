from __future__ import annotations

from pathlib import Path

from guild_discovery import (
    discover_guilds,
    select_guilds_around_own,
    write_discovered_guilds,
)
from settings_manager import get_guild_profile_from_settings


def run_discovery_test(config: dict, logger) -> None:
    discovery = config.get("comparison", {}).get("discovery", {})
    saved_profile = get_guild_profile_from_settings()

    own_guild = (discovery.get("own_guild") or (saved_profile.name if saved_profile else "")).strip()
    own_realm = (discovery.get("own_realm") or (saved_profile.realm if saved_profile else "")).strip()
    own_region = (discovery.get("own_region") or (saved_profile.region if saved_profile else "EU")).strip().upper()

    logger.print("Discovery-only test selected.")
    logger.print("This does not fetch WCL reports or spend WCL API points.")
    logger.print(f"Target guild: {own_guild}-{own_realm}-{own_region}" if own_guild and own_realm else "Target guild not set.")

    discovered = discover_guilds(config, logger=logger)
    selected = select_guilds_around_own(
        guilds=discovered,
        own_guild=own_guild,
        own_realm=own_realm,
        own_region=own_region,
        above=int(discovery.get("guilds_above_own", 50)),
        below=int(discovery.get("guilds_below_own", 0)),
        max_used=int(discovery.get("max_discovered_guilds_used", 50)),
    )

    output_file = discovery.get("test_only_output_file", "output/comparison/discovery_test.csv")
    write_discovered_guilds(output_file, selected)

    logger.print(f"Total discovered guilds: {len(discovered)}")
    logger.print(f"Selected guilds around target: {len(selected)}")
    logger.print(f"Discovery test output: {output_file}")
    logger.print(f"Discovery debug output: {discovery.get('debug_output_file', 'output/comparison/discovery_debug.txt')}")
