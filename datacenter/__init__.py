"""Software da Database User 13 - a seven-database AI data center.

Typical use:

    from datacenter import DataCenter

    with DataCenter("storage") as center:
        center.provision()
        center.add_document("Notes", "some text to index")
        center.search("notes")
"""

from .config import (
    DATABASES,
    DATABASE_KEYS,
    DATA_CENTER_NAME,
    DATA_CENTER_SLUG,
    VERSION,
    DatabaseSpec,
)
from .core import DataCenter
from .engine import Database
from .loader import DATA_DIR, DataFileError, load_all, validate
from .seed import seed

__all__ = [
    "DATABASES",
    "DATA_DIR",
    "DataFileError",
    "DATABASE_KEYS",
    "DATA_CENTER_NAME",
    "DATA_CENTER_SLUG",
    "DataCenter",
    "Database",
    "DatabaseSpec",
    "VERSION",
    "load_all",
    "seed",
    "validate",
]
__version__ = VERSION
