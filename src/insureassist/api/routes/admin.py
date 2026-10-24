"""GET /admin/evaluations (admins only): the saved evaluation reports, newest first.
Reports are the JSON files written by `insureassist eval` and `eval-answers`."""

import json
from typing import Annotated

from fastapi import APIRouter, Depends

from insureassist.api.deps import ServicesDep, require_role
from insureassist.api.schemas import EvaluationList, EvaluationSummary
from insureassist.db.models import User

router = APIRouter(prefix="/admin", tags=["admin"])
Admin = Annotated[User, Depends(require_role("admin"))]


@router.get("/evaluations", response_model=EvaluationList)
def list_evaluations(_: Admin, services: ServicesDep) -> EvaluationList:
    items: list[EvaluationSummary] = []
    for folder in services.evaluation_dirs:
        for path in sorted(folder.glob("*.json")):
            report = json.loads(path.read_text(encoding="utf-8"))
            answers = "summary" in report
            items.append(
                EvaluationSummary(
                    name=report.get("name", path.stem),
                    created_at=report.get("created_at", ""),
                    kind="answers" if answers else "retrieval",
                    dataset=str(report.get("config", {}).get("dataset", report.get("dataset", ""))),
                    config=report.get("config", {}),
                    metrics=report.get("summary") if answers else report.get("averages", {}),
                )
            )
    items.sort(key=lambda item: item.created_at, reverse=True)
    return EvaluationList(items=items, total=len(items))
