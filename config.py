"""Central configuration: data directory and shared path constants.

Set NO_HAZE_DATA_DIR to a persistent directory (e.g. C:\\no-haze-stargaze-data on Windows).
Falls back to the project root for local development.
"""

import os
from pathlib import Path

_project_root = Path(__file__).parent
DATA_DIR: Path = Path(os.getenv("NO_HAZE_DATA_DIR", str(_project_root))).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
