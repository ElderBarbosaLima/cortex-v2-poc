"""Record ids: ULIDs stored and exposed as UUIDs (docs/discovery.md 6.1).

A ULID is 48 bits of millisecond time + 80 random bits. Written as a UUID it
still sorts by creation time across milliseconds, and it is a valid UUID at the
API boundary (house rule: UUIDs out, numeric keys never).
"""

from ulid import ULID


def new_record_id() -> str:
    return str(ULID().to_uuid())
