from __future__ import annotations

from typing import Any
from urllib.parse import quote

import requests


V1_REPORT_FIGHTS_URL = "https://www.warcraftlogs.com/v1/report/fights/{report_code}"
V1_GUILD_REPORTS_URL = "https://www.warcraftlogs.com/v1/reports/guild/{guild_name}/{realm}/{region}"
V1_ZONES_URL = "https://www.warcraftlogs.com/v1/zones"

JsonDict = dict[str, Any]


class WCLApiError(Exception):
    pass


class WCLV1Client:
    """
    Warcraft Logs v1 API-key client.

    v1 is deprecated by Warcraft Logs, but it is simple because it uses one API key.
    """

    def __init__(self, api_key: str | None):
        if not api_key:
            raise WCLApiError("Missing Warcraft Logs v1 API key.")

        self.api_key = api_key


    def fetch_guild_reports(
        self,
        guild_name: str,
        realm: str,
        region: str,
        start_ms: int,
        end_ms: int,
    ) -> list[JsonDict]:
        url = V1_GUILD_REPORTS_URL.format(
            guild_name=quote(guild_name, safe=""),
            realm=quote(realm, safe=""),
            region=quote(region.upper(), safe=""),
        )

        try:
            response = requests.get(
                url,
                params={
                    "api_key": self.api_key,
                    "start": start_ms,
                    "end": end_ms,
                },
                timeout=60,
            )
        except requests.RequestException as e:
            raise WCLApiError(
                "Could not connect to Warcraft Logs while fetching guild reports."
            ) from e

        if response.status_code == 401 or response.status_code == 403:
            raise WCLApiError("Invalid Warcraft Logs API key or guild report access denied.")

        if response.status_code == 404:
            raise WCLApiError(
                f"Guild reports not found for {guild_name}-{realm}-{region}. "
                "Check spelling, realm, and region."
            )

        if response.status_code != 200:
            raise WCLApiError(
                f"Warcraft Logs returned HTTP {response.status_code} while fetching guild reports: "
                f"{response.text[:300]}"
            )

        try:
            data = response.json()
        except ValueError as e:
            raise WCLApiError("Warcraft Logs returned a non-JSON guild reports response.") from e

        if not isinstance(data, list):
            raise WCLApiError("Guild reports response was not a list.")

        return data

    def fetch_zones(self) -> list[JsonDict]:
        try:
            response = requests.get(
                V1_ZONES_URL,
                params={"api_key": self.api_key},
                timeout=60,
            )
        except requests.RequestException as e:
            raise WCLApiError("Could not connect to Warcraft Logs while fetching zones.") from e

        if response.status_code != 200:
            raise WCLApiError(
                f"Could not fetch Warcraft Logs zones. HTTP {response.status_code}: {response.text[:300]}"
            )

        try:
            data = response.json()
        except ValueError as e:
            raise WCLApiError("Warcraft Logs returned a non-JSON zones response.") from e

        if not isinstance(data, list):
            raise WCLApiError("Zones response was not a list.")

        return data

    def fetch_report_fights(self, report_code: str, mythic_difficulty: int = 5) -> JsonDict:
        url = V1_REPORT_FIGHTS_URL.format(report_code=report_code)

        try:
            response = requests.get(
                url,
                params={"api_key": self.api_key},
                timeout=60,
            )
        except requests.RequestException as e:
            raise WCLApiError(
                "Could not connect to Warcraft Logs. Check your internet connection."
            ) from e

        if response.status_code == 401 or response.status_code == 403:
            raise WCLApiError("Invalid Warcraft Logs API key or access denied.")

        if response.status_code == 404:
            raise WCLApiError(
                f"Report not found: {report_code}. Check the report code/link."
            )

        if response.status_code != 200:
            raise WCLApiError(
                f"Warcraft Logs returned HTTP {response.status_code}: {response.text[:300]}"
            )

        try:
            data = response.json()
        except ValueError as e:
            raise WCLApiError("Warcraft Logs returned a non-JSON response.") from e

        if "error" in data:
            raise WCLApiError(f"Warcraft Logs error: {data['error']}")

        if "fights" not in data:
            raise WCLApiError(
                f"Report response did not include fights for code: {report_code}. "
                "The report may be private or unavailable."
            )

        return normalise_v1_report(report_code, data)


def normalise_v1_report(report_code: str, data: JsonDict) -> JsonDict:
    report_start = int(data.get("start", data.get("startTime", 0)))
    report_end = int(data.get("end", data.get("endTime", 0)))

    normalised_fights: list[JsonDict] = []

    for fight in data.get("fights", []):
        encounter_id = (
            fight.get("boss")
            or fight.get("encounterID")
            or fight.get("encounterId")
        )

        if not encounter_id:
            continue

        difficulty = fight.get("difficulty")
        start_time = fight.get("start_time", fight.get("startTime"))
        end_time = fight.get("end_time", fight.get("endTime"))

        if start_time is None or end_time is None:
            continue

        normalised_fights.append(
            {
                "id": fight.get("id", fight.get("fightID", len(normalised_fights) + 1)),
                "encounterID": int(encounter_id),
                "name": fight.get("name", f"Encounter {encounter_id}"),
                "difficulty": difficulty,
                "kill": bool(fight.get("kill", False)),
                "startTime": int(start_time),
                "endTime": int(end_time),
            }
        )

    return {
        "code": report_code,
        "title": data.get("title", report_code),
        "startTime": report_start,
        "endTime": report_end,
        "fights": normalised_fights,
    }
