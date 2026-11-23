"""Domain packs: everything workflow-specific lives here, never in the core (D18).

Each pack brings its entity types, entity resolver, rule conditions, synthetic
data and golden set (interface: `base.DomainPack`). The core finds packs by
discovery, never by name: every subpackage that exports `PACK` is registered.
"""

from __future__ import annotations

import importlib
import pkgutil

from app.domain_packs.base import DomainPack, register_pack


def discover() -> list[DomainPack]:
    """Register every pack under app/domain_packs (a subpackage exporting `PACK`)."""
    found: list[DomainPack] = []
    for info in pkgutil.iter_modules(__path__):
        if not info.ispkg:
            continue
        module = importlib.import_module(f"{__name__}.{info.name}")
        pack = getattr(module, "PACK", None)
        if pack is not None:
            found.append(register_pack(pack))
    return found
