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
from .seed import seed

__all__ = [
    "DATABASES",
    "DATABASE_KEYS",
    "DATA_CENTER_NAME",
    "DATA_CENTER_SLUG",
    "DataCenter",
    "Database",
    "DatabaseSpec",
    "VERSION",
    "seed",
]
__version__ = VERSION
