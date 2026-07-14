from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile


REPOSITORY = "Sprit158/WCL-Reclear-Tracker"
LATEST_RELEASE_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
APP_ROOT = Path(__file__).resolve().parent
VERSION_FILE = APP_ROOT / "version.txt"
MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
LAUNCHER_FILE = Path("START_WCL_RECLEAR_TRACKER.bat")
PENDING_LAUNCHER_FILE = Path("START_WCL_RECLEAR_TRACKER.bat.update")

# These belong to the local installation and must never be replaced by a release.
PRESERVED_FILES = {
    Path(".env"),
    Path("reports.txt"),
    Path("extra_reports.txt"),
    Path("comparison_guilds.csv"),
    Path("data/wowprogress_1_2_day_backup.csv"),
}

PRESERVED_FOLDERS = {
    "cache",
    "guild_reports",
    "logs",
    "output",
    "schedule_report_lists",
    "__pycache__",
}


class UpdateError(RuntimeError):
    pass


def version_tuple(value: str) -> tuple[int, ...]:
    cleaned = value.strip().lower().lstrip("v")
    parts: list[int] = []
    for part in cleaned.split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        parts.append(int(digits or 0))
    return tuple(parts)


def current_version() -> str:
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise UpdateError(f"Could not read {VERSION_FILE.name}: {exc}") from exc


def request_json(url: str) -> dict[str, Any]:
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "WCL-ReclearTracker-Updater",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise UpdateError(f"GitHub update check failed: HTTP {exc.code}.") from exc
    except (URLError, TimeoutError) as exc:
        raise UpdateError(f"Could not connect to GitHub: {exc}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError("GitHub returned an invalid update response.") from exc


def latest_release() -> tuple[str, str]:
    payload = request_json(LATEST_RELEASE_API)
    tag = str(payload.get("tag_name") or "").strip()
    if not tag:
        raise UpdateError("The latest GitHub release has no version tag.")

    expected_name = f"v{tag.lstrip('vV')}.zip"
    assets = payload.get("assets") or []
    for asset in assets:
        if str(asset.get("name") or "").lower() == expected_name.lower():
            url = str(asset.get("browser_download_url") or "").strip()
            if url:
                return tag.lstrip("vV"), url

    raise UpdateError(f"The latest release does not contain {expected_name}.")


def download_file(url: str, destination: Path) -> None:
    request = Request(url, headers={"User-Agent": "WCL-ReclearTracker-Updater"})
    try:
        with urlopen(request, timeout=60) as response, destination.open("wb") as output:
            declared_size = int(response.headers.get("Content-Length") or 0)
            if declared_size > MAX_DOWNLOAD_BYTES:
                raise UpdateError("The update ZIP is unexpectedly large.")

            downloaded = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                downloaded += len(chunk)
                if downloaded > MAX_DOWNLOAD_BYTES:
                    raise UpdateError("The update ZIP exceeded the safe size limit.")
                output.write(chunk)
    except HTTPError as exc:
        raise UpdateError(f"Update download failed: HTTP {exc.code}.") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise UpdateError(f"Could not download the update: {exc}") from exc


def safe_extract(zip_path: Path, destination: Path) -> None:
    try:
        with zipfile.ZipFile(zip_path) as archive:
            destination_resolved = destination.resolve()
            for member in archive.infolist():
                target = (destination / member.filename).resolve()
                if target != destination_resolved and destination_resolved not in target.parents:
                    raise UpdateError("The update ZIP contains an unsafe file path.")
            archive.extractall(destination)
    except zipfile.BadZipFile as exc:
        raise UpdateError("The downloaded update is not a valid ZIP file.") from exc


def find_release_root(extracted: Path) -> Path:
    candidates = [path for path in extracted.iterdir() if path.is_dir()]
    direct_version = extracted / "version.txt"
    if direct_version.exists():
        return extracted
    versioned = [path for path in candidates if (path / "version.txt").exists()]
    if len(versioned) == 1:
        return versioned[0]
    raise UpdateError("The update ZIP does not contain one recognisable app folder.")


def deep_merge(defaults: Any, local: Any) -> Any:
    if isinstance(defaults, dict) and isinstance(local, dict):
        merged = dict(defaults)
        for key, value in local.items():
            merged[key] = deep_merge(merged[key], value) if key in merged else value
        return merged
    return local


def merged_config_bytes(release_root: Path) -> bytes | None:
    new_path = release_root / "config.json"
    local_path = APP_ROOT / "config.json"
    if not new_path.exists():
        return None
    if not local_path.exists():
        return new_path.read_bytes()
    try:
        defaults = json.loads(new_path.read_text(encoding="utf-8"))
        local = json.loads(local_path.read_text(encoding="utf-8"))
        merged = deep_merge(defaults, local)
        return (json.dumps(merged, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    except (OSError, json.JSONDecodeError) as exc:
        raise UpdateError(f"Could not merge config.json safely: {exc}") from exc


def should_preserve(relative: Path) -> bool:
    if relative in PRESERVED_FILES:
        return True
    return bool(relative.parts and relative.parts[0] in PRESERVED_FOLDERS)


def apply_release(release_root: Path) -> int:
    config_bytes = merged_config_bytes(release_root)
    copied = 0

    for source in release_root.rglob("*"):
        if not source.is_file():
            continue
        relative = source.relative_to(release_root)
        destination = APP_ROOT / relative
        # Preserve personal files that already exist, but allow a personalised
        # package to install a missing local dataset/configuration file.
        if should_preserve(relative) and destination.exists():
            continue
        if relative == Path("config.json"):
            continue

        # cmd.exe reads a running batch file from disk as it executes it. If
        # we replace that file in place, its byte offset can land in the middle
        # of a new line (for example, THON_CMD instead of %PYTHON_CMD%). Stage
        # the new launcher and replace it only after the parent cmd.exe exits.
        if relative == LAUNCHER_FILE and destination.exists():
            pending = APP_ROOT / PENDING_LAUNCHER_FILE
            shutil.copy2(source, pending)
            copied += 1
            continue

        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied += 1

    if config_bytes is not None:
        temporary = APP_ROOT / "config.json.update"
        temporary.write_bytes(config_bytes)
        temporary.replace(APP_ROOT / "config.json")
        copied += 1

    return copied


def schedule_pending_launcher_replace() -> bool:
    pending = APP_ROOT / PENDING_LAUNCHER_FILE
    destination = APP_ROOT / LAUNCHER_FILE
    if not pending.exists():
        return False

    parent_pid = os.getppid()
    helper = r"""
import os
import sys
import time

parent_pid = int(sys.argv[1])
pending = sys.argv[2]
destination = sys.argv[3]

if os.name == "nt":
    import ctypes
    SYNCHRONIZE = 0x00100000
    INFINITE = 0xFFFFFFFF
    handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, parent_pid)
    if handle:
        ctypes.windll.kernel32.WaitForSingleObject(handle, INFINITE)
        ctypes.windll.kernel32.CloseHandle(handle)
else:
    while True:
        try:
            os.kill(parent_pid, 0)
        except OSError:
            break
        time.sleep(0.25)

os.replace(pending, destination)
"""
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        )
    else:
        kwargs["start_new_session"] = True

    try:
        subprocess.Popen(
            [sys.executable, "-c", helper, str(parent_pid), str(pending), str(destination)],
            **kwargs,
        )
    except OSError as exc:
        raise UpdateError(f"Could not schedule the launcher replacement: {exc}") from exc
    return True


def run_update() -> int:
    installed = current_version()
    print(f"Installed version: v{installed}")
    print("Checking GitHub for updates...")
    latest, asset_url = latest_release()
    print(f"Latest version:    v{latest}")

    if version_tuple(latest) <= version_tuple(installed):
        print("You already have the latest version.")
        return 0

    print(f"Downloading v{latest}...")
    with tempfile.TemporaryDirectory(prefix="wcl_reclear_update_") as temporary_folder:
        temporary = Path(temporary_folder)
        zip_path = temporary / f"v{latest}.zip"
        extracted = temporary / "extracted"
        extracted.mkdir()
        download_file(asset_url, zip_path)
        safe_extract(zip_path, extracted)
        release_root = find_release_root(extracted)
        copied = apply_release(release_root)

    installed_after = current_version()
    if version_tuple(installed_after) != version_tuple(latest):
        raise UpdateError(
            f"Files were copied, but version.txt says v{installed_after} instead of v{latest}."
        )

    launcher_deferred = schedule_pending_launcher_replace()

    print(f"Updated successfully to v{latest} ({copied} program files replaced).")
    print("Your local guild data, reports, cache, database and personal settings were preserved.")
    if launcher_deferred:
        print("The launcher update will finish automatically after this Command Prompt window closes.")
    return 10


def main() -> int:
    try:
        return run_update()
    except UpdateError as exc:
        print(f"Update failed: {exc}")
        return 1
    except Exception as exc:
        print(f"Update failed unexpectedly: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
