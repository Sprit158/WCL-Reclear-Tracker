from __future__ import annotations

from datetime import datetime
from pathlib import Path


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
        print(message)
        self.write(message)
