from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import time

import requests


type JsonDict = dict[str, Any]


class WCLV2ApiError(Exception):
    pass


@dataclass(slots=True)
class WCLV2Config:
    token_url: str = "https://www.warcraftlogs.com/oauth/token"
    graphql_url: str = "https://www.warcraftlogs.com/api/v2/client"
    token_cache_seconds_safety_margin: int = 120


@dataclass(slots=True)
class WCLV2Credentials:
    client_id: str
    client_secret: str


@dataclass(slots=True)
class WCLV2Token:
    access_token: str
    expires_at_utc: int
    token_type: str = "Bearer"


class WCLV2Client:
    def __init__(
        self,
        credentials: WCLV2Credentials,
        config: WCLV2Config | None = None,
        token: WCLV2Token | None = None,
    ) -> None:
        self.credentials = credentials
        self.config = config or WCLV2Config()
        self.token = token

    def token_is_valid(self) -> bool:
        if not self.token:
            return False

        return int(time.time()) < int(self.token.expires_at_utc) - self.config.token_cache_seconds_safety_margin

    def fetch_access_token(self) -> WCLV2Token:
        try:
            response = requests.post(
                self.config.token_url,
                auth=(self.credentials.client_id, self.credentials.client_secret),
                data={"grant_type": "client_credentials"},
                timeout=30,
            )
        except requests.RequestException as e:
            raise WCLV2ApiError(f"Could not connect to WCL OAuth token endpoint: {e}") from e

        if response.status_code in (401, 403):
            raise WCLV2ApiError("WCL v2 OAuth rejected the Client ID / Client Secret.")

        if response.status_code >= 400:
            raise WCLV2ApiError(f"WCL OAuth token request failed: HTTP {response.status_code} - {response.text[:500]}")

        try:
            payload = response.json()
        except ValueError as e:
            raise WCLV2ApiError(f"WCL OAuth token response was not JSON: {response.text[:500]}") from e

        access_token = payload.get("access_token")
        expires_in = int(payload.get("expires_in", 0))
        token_type = payload.get("token_type", "Bearer")

        if not access_token:
            raise WCLV2ApiError(f"WCL OAuth token response did not include access_token: {payload}")

        self.token = WCLV2Token(
            access_token=access_token,
            expires_at_utc=int(time.time()) + max(expires_in, 0),
            token_type=token_type,
        )
        return self.token

    def ensure_token(self) -> WCLV2Token:
        if self.token_is_valid():
            assert self.token is not None
            return self.token

        return self.fetch_access_token()

    def graphql(self, query: str, variables: JsonDict | None = None) -> JsonDict:
        token = self.ensure_token()

        headers = {
            "Authorization": f"{token.token_type} {token.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        body = {"query": query, "variables": variables or {}}

        try:
            response = requests.post(
                self.config.graphql_url,
                json=body,
                headers=headers,
                timeout=45,
            )
        except requests.RequestException as e:
            raise WCLV2ApiError(f"Could not connect to WCL GraphQL endpoint: {e}") from e

        if response.status_code == 401:
            # Token may have been revoked/expired earlier than expected. Refresh once.
            self.token = None
            token = self.fetch_access_token()
            headers["Authorization"] = f"{token.token_type} {token.access_token}"
            response = requests.post(
                self.config.graphql_url,
                json=body,
                headers=headers,
                timeout=45,
            )

        if response.status_code >= 400:
            raise WCLV2ApiError(f"WCL GraphQL request failed: HTTP {response.status_code} - {response.text[:500]}")

        try:
            payload = response.json()
        except ValueError as e:
            raise WCLV2ApiError(f"WCL GraphQL response was not JSON: {response.text[:500]}") from e

        if payload.get("errors"):
            raise WCLV2ApiError(f"WCL GraphQL returned errors: {payload['errors']}")

        return payload

    def test_query(self) -> JsonDict:
        # Minimal query intended only to verify OAuth + GraphQL connectivity.
        query = """
        query {
          rateLimitData {
            limitPerHour
            pointsSpentThisHour
            pointsResetIn
          }
        }
        """
        return self.graphql(query)

    def fetch_guild_reports_page(
        self,
        guild_name: str,
        guild_server_slug: str,
        guild_server_region: str,
        start_time: int | None = None,
        end_time: int | None = None,
        page: int = 1,
        limit: int = 100,
    ) -> JsonDict:
        query = """
        query GuildReports(
          $guildName: String!,
          $guildServerSlug: String!,
          $guildServerRegion: String!,
          $startTime: Float,
          $endTime: Float,
          $page: Int,
          $limit: Int
        ) {
          reportData {
            reports(
              guildName: $guildName,
              guildServerSlug: $guildServerSlug,
              guildServerRegion: $guildServerRegion,
              startTime: $startTime,
              endTime: $endTime,
              page: $page,
              limit: $limit
            ) {
              current_page
              last_page
              has_more_pages
              data {
                code
                title
                startTime
                endTime
                zone {
                  id
                  name
                }
              }
            }
          }
        }
        """
        variables = {
            "guildName": guild_name,
            "guildServerSlug": guild_server_slug,
            "guildServerRegion": guild_server_region.upper(),
            "startTime": float(start_time) if start_time is not None else None,
            "endTime": float(end_time) if end_time is not None else None,
            "page": page,
            "limit": limit,
        }
        return self.graphql(query, variables)

    def fetch_guild_reports(
        self,
        guild_name: str,
        guild_server_slug: str,
        guild_server_region: str,
        start_time: int | None = None,
        end_time: int | None = None,
        limit: int = 100,
        max_pages: int = 20,
    ) -> list[JsonDict]:
        reports: list[JsonDict] = []

        for page in range(1, max_pages + 1):
            payload = self.fetch_guild_reports_page(
                guild_name=guild_name,
                guild_server_slug=guild_server_slug,
                guild_server_region=guild_server_region,
                start_time=start_time,
                end_time=end_time,
                page=page,
                limit=limit,
            )
            paginator = payload.get("data", {}).get("reportData", {}).get("reports", {})
            data = paginator.get("data") or []
            reports.extend(data)

            if not paginator.get("has_more_pages"):
                break

        return reports

    def fetch_report_fights(self, report_code: str) -> JsonDict:
        """
        Fetch report fight summaries and normalise them to:
        {code,title,startTime,endTime,fights:[{name,difficulty,kill,startTime,endTime,encounterID}]}
        """
        query = """
        query ReportFights($code: String!) {
          reportData {
            report(code: $code) {
              code
              title
              startTime
              endTime
              fights {
                id
                encounterID
                name
                difficulty
                kill
                startTime
                endTime
              }
            }
          }
        }
        """
        payload = self.graphql(query, {"code": report_code})
        report = payload.get("data", {}).get("reportData", {}).get("report")
        if not report:
            raise WCLV2ApiError(f"WCL v2 report lookup returned no report for code: {report_code}")

        fights = []
        for fight in report.get("fights") or []:
            start = fight.get("startTime")
            end = fight.get("endTime")
            encounter = fight.get("encounterID") or fight.get("encounterId") or fight.get("boss")
            if start is None or end is None:
                continue
            fights.append(
                {
                    "id": fight.get("id", len(fights)+1),
                    "encounterID": int(encounter) if encounter else None,
                    "name": fight.get("name") or "",
                    "difficulty": fight.get("difficulty"),
                    "kill": bool(fight.get("kill", False)),
                    "startTime": int(start),
                    "endTime": int(end),
                }
            )

        return {
            "code": report.get("code") or report_code,
            "title": report.get("title") or report_code,
            "startTime": int(report.get("startTime") or 0),
            "endTime": int(report.get("endTime") or 0),
            "fights": fights,
        }

