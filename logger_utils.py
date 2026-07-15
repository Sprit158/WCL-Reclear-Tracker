from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys


class RunLogger:
    def __init__(
        self,
        enabled: bool = True,
        folder: str = "logs",
        latest_log: str = "latest_run.txt",
    ):
        self.enabled = enabled
        self.folder = Path(folder)
        self.path = self.folder / latest_log

        if self.enabled:
            self.folder.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                f"WCL Reclear Tracker run started: {datetime.now().isoformat(timespec='seconds')}\n\n",
                encoding="utf-8",
            )

    def write(self, message: str) -> None:
        if not self.enabled:
            return

        with self.path.open("a", encoding="utf-8") as f:
            f.write(message + "\n")

    def print(self, message: str = "") -> None:
        # A Windows console commonly defaults to cp1252. Guild names and
        # realms can contain characters outside that code page; logging must
        # never abort a schedule scan because one name cannot be rendered.
        text = str(message)
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        try:
            console_text = text.encode(encoding, errors="replace").decode(encoding)
        except (LookupError, UnicodeError):
            console_text = text.encode("utf-8", errors="replace").decode("utf-8")
        print(console_text)
        self.write(text)
