from __future__ import annotations

import importlib.util
import subprocess
import sys


REQUIRED_PACKAGES: dict[str, str] = {
    "requests": "requests",
    "pandas": "pandas",
    "openpyxl": "openpyxl",
    "dotenv": "python-dotenv",
}


def is_module_available(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def ensure_dependencies(auto_install: bool = True) -> None:
    missing = [
        pip_name
        for module_name, pip_name in REQUIRED_PACKAGES.items()
        if not is_module_available(module_name)
    ]

    if not missing:
        return

    print("Missing required Python package(s):")
    for package in missing:
        print(f"  - {package}")

    if not auto_install:
        raise RuntimeError(
            "Missing required packages and auto_install_dependencies is disabled."
        )

    print("Installing missing package(s)...")

    cmd = [sys.executable, "-m", "pip", "install", *missing]
    result = subprocess.run(cmd, check=False)

    if result.returncode != 0:
        raise RuntimeError(
            "Package installation failed. Try running: "
            f"{sys.executable} -m pip install {' '.join(missing)}"
        )

    print("Dependencies installed.")
