"""Build standalone executable for TelegaBot using PyInstaller.

Usage:
  python build_exe.py

Optional env vars:
  EXE_NAME=telegabot
  ONEFILE=1|0
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent
    bot_file = root / "bot.py"
    data_file = root / "content_ideas_2026_ru.json"
    env_example = root / ".env.example"

    if not bot_file.exists():
        print("bot.py not found", file=sys.stderr)
        return 1

    pyinstaller = shutil.which("pyinstaller")
    if not pyinstaller:
        print(
            "PyInstaller is not installed. Install it with:\n"
            "  pip install pyinstaller\n"
            "or\n"
            "  pip install -r requirements-build.txt",
            file=sys.stderr,
        )
        return 2

    name = os.getenv("EXE_NAME", "telegabot")
    onefile = os.getenv("ONEFILE", "1").lower() in {"1", "true", "yes", "on"}
    sep = ";" if os.name == "nt" else ":"

    cmd = [
        pyinstaller,
        "--noconfirm",
        "--clean",
        "--name",
        name,
        "--console",
        "--collect-submodules",
        "telegram",
        "--collect-submodules",
        "telegram.ext",
    ]

    if onefile:
        cmd.append("--onefile")

    if data_file.exists():
        cmd += ["--add-data", f"{data_file}{sep}."]
    if env_example.exists():
        cmd += ["--add-data", f"{env_example}{sep}."]

    cmd.append(str(bot_file))

    print("Running:", " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=root)

    dist = root / "dist"
    built = dist / (name + (".exe" if os.name == "nt" else ""))
    if built.exists():
        print(f"\nBuild complete: {built}")
        print("Copy .env рядом с exe, заполните TELEGRAM_BOT_TOKEN и TELEGRAM_CHANNEL_ID, затем запускайте файл.")
    else:
        print("Build finished, but output path was not detected automatically.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
