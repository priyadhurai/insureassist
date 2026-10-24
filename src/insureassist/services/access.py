"""Who may read which document. The ONE rule, used everywhere:

    a user may read a document if they are active AND
      (they are an admin, OR they own it, OR it is shared with them, OR with their role)

The vector database is not an access-control system: this set of document ids is
computed here, from PostgreSQL, and passed INTO every search as a filter, so chunks
of other documents are never even compared with the question.
"""

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from insureassist.db.models import AuditEvent, Document, DocumentAccess, User


def readable_document_ids(session: Session, user: User) -> set[str]:
    if not user.is_active:
        return set()
    if user.role == "admin":
        return set(session.scalars(select(Document.id)))
    shared = select(DocumentAccess.document_id).where(
        or_(DocumentAccess.user_id == user.id, DocumentAccess.role == user.role)
    )
    query = select(Document.id).where(or_(Document.owner_id == user.id, Document.id.in_(shared)))
    return set(session.scalars(query))


def can_read(session: Session, user: User, document_id: str) -> bool:
    return document_id in readable_document_ids(session, user)


def can_delete(user: User, document: Document) -> bool:
    return user.role == "admin" or (user.role == "editor" and document.owner_id == user.id)


def audit(
    session: Session,
    action: str,
    outcome: str,
    actor: User | None = None,
    target_type: str = "",
    target_id: str | None = None,
    request_id: str | None = None,
    **details: object,
) -> None:
    """Record a security-relevant event. `details` must hold safe values only
    (ids, counts, hashes), never passwords, tokens, questions or document text."""
    session.add(
        AuditEvent(
            actor_user_id=actor.id if actor else None,
            action=action,
            target_type=target_type,
            target_id=target_id,
            outcome=outcome,
            request_id=request_id,
            details=details,
        )
    )
