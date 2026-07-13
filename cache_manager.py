from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
import json


type JsonDict = dict[str, Any]
type FetchReportFunc = Callable[[str], JsonDict]


class CacheError(Exception):
    pass


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class ReportCache:
    """
    Reusable cache for Warcraft Logs report data.

    Future app versions should read the "data" field.
    Metadata may be expanded later without breaking old cache files.
    """

    def __init__(
        self,
        folder: str = "cache/reports",
        enabled: bool = True,
        schema_version: int = 1,
        supported_schema_versions: list[int] | None = None,
    ):
        self.folder = Path(folder)
        self.enabled = enabled
        self.schema_version = schema_version
        self.supported_schema_versions = supported_schema_versions or [schema_version]

        if self.enabled:
            self.folder.mkdir(parents=True, exist_ok=True)

    def path_for_report(self, report_code: str) -> Path:
        safe_code = "".join(ch for ch in report_code if ch.isalnum())
        return self.folder / f"{safe_code}.json"

    def check_cache_compatibility(self, wrapper: JsonDict, path: Path) -> None:
        schema = wrapper.get("schema_version", 0)

        if schema not in self.supported_schema_versions:
            raise CacheError(
                f"Cache schema {schema} is not supported by this version. "
                f"Supported: {self.supported_schema_versions}. File: {path}"
            )

        if wrapper.get("cache_type") != "report_fights":
            raise CacheError(f"Unexpected cache_type in {path}")

    def load_report(self, report_code: str) -> JsonDict | None:
        if not self.enabled:
            return None

        path = self.path_for_report(report_code)

        if not path.exists():
            return None

        try:
            wrapper = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise CacheError(f"Cache file is not valid JSON: {path} ({e})") from e

        if not isinstance(wrapper, dict):
            raise CacheError(f"Cache file has invalid structure: {path}")

        self.check_cache_compatibility(wrapper, path)

        if "data" not in wrapper:
            raise CacheError(f"Cache file missing 'data' field: {path}")

        return wrapper["data"]

    def save_report(
        self,
        report_code: str,
        report_data: JsonDict,
        query: JsonDict | None = None,
    ) -> Path | None:
        if not self.enabled:
            return None

        path = self.path_for_report(report_code)

        wrapper = {
            "schema_version": self.schema_version,
            "source": "warcraftlogs",
            "cache_type": "report_fights",
            "report_code": report_code,
            "fetched_at_utc": utc_now_iso(),
            "query": query or {},
            "data": report_data,
        }

        path.write_text(
            json.dumps(wrapper, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        return path

    def fetch_or_load_report(
        self,
        report_code: str,
        fetch_func: FetchReportFunc,
        force_refresh: bool = False,
        query: JsonDict | None = None,
    ) -> tuple[JsonDict, str]:
        if self.enabled and not force_refresh:
            cached = self.load_report(report_code)
            if cached is not None:
                return cached, "cache"

        report_data = fetch_func(report_code)
        self.save_report(report_code, report_data, query=query)

        return report_data, "api"
