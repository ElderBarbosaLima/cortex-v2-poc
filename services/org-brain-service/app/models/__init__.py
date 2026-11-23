from app.models.base import Base
from app.models.immutable import LedgerImmutableError, enforce_append_only
from app.models.ledger import (
    AssuranceLevel,
    Enforcement,
    Kind,
    MemoryRecord,
    Outbox,
    RecordKey,
    SensitivityClass,
    Status,
)
from app.models.review import ReviewCase, ReviewCaseItem, ReviewDecision
from app.models.served_pack import (
    Correction,
    Erasure,
    EvidenceExport,
    ServedPack,
    ServedPackItem,
    Verification,
)
from app.models.tenant import Tenant

enforce_append_only(MemoryRecord)
enforce_append_only(ReviewDecision)
enforce_append_only(ServedPack)
enforce_append_only(ServedPackItem)
enforce_append_only(Verification)
enforce_append_only(Correction)
enforce_append_only(Erasure)

__all__ = [
    "AssuranceLevel",
    "Base",
    "Correction",
    "Enforcement",
    "Erasure",
    "EvidenceExport",
    "Kind",
    "LedgerImmutableError",
    "MemoryRecord",
    "Outbox",
    "RecordKey",
    "ReviewCase",
    "ReviewCaseItem",
    "ReviewDecision",
    "SensitivityClass",
    "ServedPack",
    "ServedPackItem",
    "Status",
    "Tenant",
    "Verification",
]
