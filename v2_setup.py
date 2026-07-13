from __future__ import annotations

import os
from typing import Any

from api.wcl_api_v2 import (
    WCLV2ApiError,
    WCLV2Client,
    WCLV2Config,
    WCLV2Credentials as ApiCredentials,
    WCLV2Token as ApiToken,
)
from settings_manager import (
    SettingsError,
    get_v2_token_from_settings,
    resolve_wcl_v2_credentials,
    save_v2_token_to_settings,
    WCLV2Token as SettingsToken,
)


JsonDict = dict[str, Any]


def make_v2_config(config: JsonDict) -> WCLV2Config:
    v2 = config.get("api", {}).get("v2", {})
    return WCLV2Config(
        token_url=v2.get("token_url", "https://www.warcraftlogs.com/oauth/token"),
        graphql_url=v2.get("graphql_url", "https://www.warcraftlogs.com/api/v2/client"),
        token_cache_seconds_safety_margin=int(v2.get("token_cache_seconds_safety_margin", 120)),
    )


def build_v2_client(config: JsonDict) -> WCLV2Client:
    credentials = resolve_wcl_v2_credentials(
        env_client_id=os.getenv("WCL_CLIENT_ID"),
        env_client_secret=os.getenv("WCL_CLIENT_SECRET"),
        use_global_settings=bool(config.get("settings", {}).get("use_global_settings", True)),
    )

    saved_token = get_v2_token_from_settings()
    api_token = None
    if saved_token:
        api_token = ApiToken(
            access_token=saved_token.access_token,
            expires_at_utc=saved_token.expires_at_utc,
            token_type=saved_token.token_type,
        )

    return WCLV2Client(
        credentials=ApiCredentials(
            client_id=credentials.client_id,
            client_secret=credentials.client_secret,
        ),
        config=make_v2_config(config),
        token=api_token,
    )


def save_client_token(client: WCLV2Client) -> None:
    if client.token:
        save_v2_token_to_settings(
            SettingsToken(
                access_token=client.token.access_token,
                expires_at_utc=client.token.expires_at_utc,
                token_type=client.token.token_type,
            )
        )


def run_v2_setup_test(config: JsonDict, logger) -> None:
    try:
        client = build_v2_client(config)
        logger.print("Testing WCL v2 OAuth token + GraphQL access...")
        result = client.test_query()
        save_client_token(client)

        rate_limit = result.get("data", {}).get("rateLimitData", {})
        logger.print("WCL v2 test successful.")
        if rate_limit:
            logger.print(f"Rate limit per hour: {rate_limit.get('limitPerHour')}")
            logger.print(f"Points spent this hour: {rate_limit.get('pointsSpentThisHour')}")
            logger.print(f"Points reset in: {rate_limit.get('pointsResetIn')}")
        else:
            logger.print("GraphQL responded, but no rateLimitData was returned.")
    except (SettingsError, WCLV2ApiError) as e:
        logger.print(f"WCL v2 setup/test failed: {e}")
