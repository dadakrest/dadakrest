"""Demo data so a fresh data center has something to query."""

from __future__ import annotations

from typing import Any

from . import vectors
from .config import EMBEDDING_DIM
from .core import DataCenter

CONTACTS = [
    {
        "full_name": "Ada Okoro",
        "role": "Operations lead",
        "email": "ada@northgate.example",
        "phone": "+1-555-0142",
        "organization": "Northgate Logistics",
    },
    {
        "full_name": "Ben Marsh",
        "role": "Security analyst",
        "email": "ben@harborsec.example",
        "phone": "+1-555-0177",
        "organization": "Harbor Security",
    },
    {
        "full_name": "Carla Reyes",
        "role": "Account manager",
        "email": "carla@northgate.example",
        "phone": "+1-555-0198",
        "organization": "Northgate Logistics",
    },
]

DOCUMENTS = [
    {
        "title": "Network hardening checklist",
        "body": (
            "Segment the office network, disable unused switch ports, enforce WPA3 "
            "on wireless, and rotate router admin credentials every quarter. "
            "Review firewall rules monthly and log all denied traffic."
        ),
        "source": "internal/runbooks",
        "tags": ["security", "networking"],
    },
    {
        "title": "Client onboarding process",
        "body": (
            "Collect the signed service agreement, create the client record, "
            "assign an account manager, and schedule the kickoff call within "
            "five business days of signature."
        ),
        "source": "internal/process",
        "tags": ["operations"],
    },
    {
        "title": "Quarterly invoice summary",
        "body": (
            "Invoices issued this quarter cover managed support hours, hardware "
            "procurement, and the security audit engagement. Payment terms are "
            "net thirty from the invoice date."
        ),
        "source": "finance/2026-q1",
        "tags": ["finance"],
    },
    {
        "title": "Incident response contacts",
        "body": (
            "During an incident, page the on-call security analyst first, then "
            "notify the operations lead. Escalate to the client account manager "
            "once the scope of affected systems is confirmed."
        ),
        "source": "internal/runbooks",
        "tags": ["security", "operations"],
    },
]

DATASET_RECORDS = [
    {"ticket": "SUP-1001", "category": "hardware", "hours": 2.5, "status": "closed"},
    {"ticket": "SUP-1002", "category": "network", "hours": 4.0, "status": "closed"},
    {"ticket": "SUP-1003", "category": "security", "hours": 6.25, "status": "open"},
]


def seed(center: DataCenter) -> dict[str, Any]:
    """Populate a provisioned data center with demo records.

    Returns a summary of what was written. Run `provision()` first.
    """
    center.register_model(
        vectors.LOCAL_MODEL_NAME,
        provider="built-in",
        task="embedding",
        version="1.0.0",
        dimensions=EMBEDDING_DIM,
        description="Deterministic local hashing embedder shipped with the data center.",
    )

    for contact in CONTACTS:
        center.add_contact(**contact)

    for document in DOCUMENTS:
        center.add_document(**document)

    for record in DATASET_RECORDS:
        center.add_record("support_tickets", record)

    center.submit_job("reindex", {"reason": "initial seed"}, priority=10)

    return {
        "contacts": len(CONTACTS),
        "documents": len(DOCUMENTS),
        "records": len(DATASET_RECORDS),
        "jobs": 1,
    }
