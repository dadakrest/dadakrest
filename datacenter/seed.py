"""Fill a provisioned data center from the bundled data files.

The data itself lives in `datacenter/data/*.json`; see `datacenter.loader`
for the file formats. This module only exists so `seed(center)` stays a
one-liner for callers and the CLI.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .core import DataCenter
from .loader import DATA_DIR, load_all


def seed(center: DataCenter, data_dir: Path | str = DATA_DIR) -> dict[str, Any]:
    """Load every data file into the data center. Safe to run repeatedly."""
    return load_all(center, data_dir)
