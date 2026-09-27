"""Start the API detached from the calling shell.

The dev server has to outlive the terminal that launched it, so this double-forks
into a session leader and redirects stdio to a log file.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1] / "apps" / "api"
LOG = Path("/tmp/dha_api.log")


def main() -> None:
    if os.fork() > 0:
        return
    os.setsid()
    if os.fork() > 0:
        os._exit(0)

    os.chdir(API_DIR)
    log = os.open(LOG, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)
    os.dup2(log, 1)
    os.dup2(log, 2)

    os.execv(
        str(Path(__file__).resolve().parents[1] / ".venv" / "bin" / "python"),
        [
            "python",
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8099",
            "--log-level",
            "info",
        ],
    )


if __name__ == "__main__":
    sys.exit(main())
