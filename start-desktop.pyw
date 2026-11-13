"""Compatibility entry for shortcuts and desktop processes started before relocation."""

import runpy
from pathlib import Path

if __name__ == "__main__":
    runpy.run_path(
        str(Path(__file__).resolve().parent / "frontend/start-desktop.pyw"), run_name="__main__"
    )
