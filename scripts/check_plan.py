"""Compatibility entry point for the current static design checker; no experiment."""
from pathlib import Path
import runpy

if __name__ == "__main__":
    runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "paper-design/tools/check_design.py"),
        run_name="__main__",
    )

