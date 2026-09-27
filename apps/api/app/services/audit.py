"""Write an entry to the audit log.

Every mutation that a human performs on the archive must leave a trace: who did
it, to which object, and what changed. Without this the audit log stays empty and
the archive cannot show its own history.

The helper is deliberately tiny and takes the caller's ``Session`` so that the
audit row commits together with the change it describes. If the surrounding
transaction rolls back, the audit entry rolls back with it, which is what makes
the log trustworthy rather than merely populated.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.access import AuditLog


def record(
    db: Session,
    *,
    action: str,
    actor: Any = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    detail: dict[str, Any] | None = None,
    request_id: str | None = None,
    ip_address: str | None = None,
) -> AuditLog:
    """Append one audit entry. Does not commit: the caller controls the boundary."""
    entry = AuditLog(
        actor_id=getattr(actor, "id", None),
        actor_email=getattr(actor, "email", None),
        action=action[:80],
        entity_type=(entity_type or None) and str(entity_type)[:60],
        entity_id=(entity_id or None) and str(entity_id)[:64],
        detail=detail or {},
        request_id=request_id,
        ip_address=ip_address,
    )
    db.add(entry)
    db.flush()
    return entry
