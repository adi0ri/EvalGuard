"""Small server-rendered reports over the same deterministic analysis as the API."""

from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from evalguard.db.session import get_db
from evalguard.evaluation.analysis import (
    ExperimentAnalysisNotFoundError,
    ExperimentComparisonConflictError,
    compare_experiments,
)

router = APIRouter(prefix="/reports")
SessionDependency = Annotated[Session, Depends(get_db)]
templates = Jinja2Templates(directory=Path(__file__).with_name("templates"))


@router.get(
    "/experiments/{baseline_experiment_id}/compare/{candidate_experiment_id}",
    response_class=HTMLResponse,
)
def experiment_comparison_report(
    request: Request,
    baseline_experiment_id: UUID,
    candidate_experiment_id: UUID,
    session: SessionDependency,
) -> Response:
    try:
        comparison = compare_experiments(
            session,
            baseline_experiment_id,
            candidate_experiment_id,
        )
    except ExperimentAnalysisNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Experiment not found") from exc
    except ExperimentComparisonConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    baseline_cases = {case.case_key: case for case in comparison.baseline.cases}
    candidate_cases = {case.case_key: case for case in comparison.candidate.cases}
    report_rows: list[dict[str, Any]] = [
        {
            "comparison": row,
            "baseline": baseline_cases[row.case_key],
            "candidate": candidate_cases[row.case_key],
        }
        for row in comparison.cases
    ]
    return templates.TemplateResponse(
        request=request,
        name="comparison.html",
        context={"comparison": comparison, "report_rows": report_rows},
    )
