"""POST /feedback: thumbs up/down on one of YOUR OWN answers (once per answer)."""

from fastapi import APIRouter
from sqlalchemy import select

from insureassist.api.deps import CurrentUser, DbDep
from insureassist.api.errors import ApiError
from insureassist.api.schemas import FeedbackRequest, FeedbackResponse
from insureassist.db.models import Feedback, QueryLog

router = APIRouter(prefix="/feedback", tags=["feedback"])


@router.post("", status_code=201, response_model=FeedbackResponse)
def give_feedback(body: FeedbackRequest, user: CurrentUser, db: DbDep) -> FeedbackResponse:
    entry = db.get(QueryLog, body.query_id)
    if entry is None or entry.user_id != user.id:
        raise ApiError(404, "not_found", "Query not found.")  # someone else's = not found
    exists = db.scalar(
        select(Feedback).where(Feedback.query_id == body.query_id, Feedback.user_id == user.id)
    )
    if exists is not None:
        raise ApiError(409, "duplicate_feedback", "Feedback was already given for this answer.")
    feedback = Feedback(
        query_id=body.query_id, user_id=user.id, rating=body.rating, comment=body.comment
    )
    db.add(feedback)
    db.commit()
    return FeedbackResponse(
        id=feedback.id,
        query_id=feedback.query_id,
        rating=feedback.rating,
        created_at=feedback.created_at,
    )
