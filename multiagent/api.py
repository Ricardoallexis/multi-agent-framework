from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query

from .bootstrap import build_system
from .contracts import HumanReviewRequest, HumanStepSubmission, SocialPostRequest
from .version import __version__
from .errors import (
    BudgetExceeded,
    HumanSubmissionError,
    IdempotencyConflict,
    InvalidStateTransition,
    MultiAgentError,
)


def create_app(system=None) -> FastAPI:
    system_obj = system or build_system()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if system_obj.settings.worker_enabled:
            system_obj.worker.start()
        yield
        system_obj.worker.stop()

    app = FastAPI(title="Multi-Agent Framework API", version=__version__, lifespan=lifespan)

    @app.get("/api/v1/health")
    def health():
        return {
            "status": "ok",
            "version": __version__,
            "schema_version": system_obj.db.schema_version(),
            "ollama": system_obj.adapters["ollama"].health(),
            "gemini": system_obj.adapters["gemini"].health(),
            "openai": system_obj.adapters["openai"].health(),
        }

    @app.post("/api/v1/runs", status_code=202)
    def create_run(request: SocialPostRequest):
        try:
            return system_obj.run_service.create_social_post(request)
        except IdempotencyConflict as exc:
            raise HTTPException(409, detail=str(exc)) from exc

    @app.get("/api/v1/runs")
    def list_runs(limit: int = Query(20, ge=1, le=500), status: str = "", project: str = ""):
        return system_obj.run_service.list(limit=limit, status=status, project=project)

    @app.get("/api/v1/runs/{run_id}")
    def get_run(run_id: str):
        try:
            return system_obj.run_service.get(run_id)
        except KeyError as exc:
            raise HTTPException(404, detail=str(exc)) from exc

    @app.post("/api/v1/runs/{run_id}/approve")
    def approve(run_id: str):
        try:
            return system_obj.run_service.approve(run_id)
        except KeyError as exc:
            raise HTTPException(404, detail=str(exc)) from exc
        except (InvalidStateTransition, BudgetExceeded) as exc:
            raise HTTPException(409, detail=str(exc)) from exc

    @app.post("/api/v1/runs/{run_id}/changes")
    def changes(run_id: str, review: HumanReviewRequest):
        try:
            return system_obj.run_service.changes(run_id, review.feedback)
        except KeyError as exc:
            raise HTTPException(404, detail=str(exc)) from exc
        except (InvalidStateTransition, ValueError, BudgetExceeded) as exc:
            raise HTTPException(409, detail=str(exc)) from exc

    @app.post("/api/v1/runs/{run_id}/reject")
    def reject(run_id: str, review: HumanReviewRequest):
        try:
            return system_obj.run_service.reject(run_id, review.feedback)
        except KeyError as exc:
            raise HTTPException(404, detail=str(exc)) from exc
        except InvalidStateTransition as exc:
            raise HTTPException(409, detail=str(exc)) from exc

    @app.post("/api/v1/runs/{run_id}/regenerate")
    def regenerate(run_id: str):
        try:
            return system_obj.run_service.changes(run_id, "", regenerate=True)
        except KeyError as exc:
            raise HTTPException(404, detail=str(exc)) from exc
        except (InvalidStateTransition, ValueError, BudgetExceeded) as exc:
            raise HTTPException(409, detail=str(exc)) from exc

    @app.post("/api/v1/runs/{run_id}/cancel")
    def cancel(run_id: str, force: bool = False):
        try:
            return system_obj.run_service.cancel(run_id, force=force)
        except KeyError as exc:
            raise HTTPException(404, detail=str(exc)) from exc

    @app.get("/api/v1/runs/{run_id}/human-next")
    def human_next(run_id: str):
        try:
            return system_obj.run_service.human_next(run_id)
        except KeyError as exc:
            raise HTTPException(404, detail=str(exc)) from exc
        except HumanSubmissionError as exc:
            raise HTTPException(409, detail=str(exc)) from exc

    @app.post("/api/v1/runs/{run_id}/human-submit")
    def human_submit(run_id: str, submission: HumanStepSubmission):
        try:
            return system_obj.run_service.human_submit(run_id, submission)
        except KeyError as exc:
            raise HTTPException(404, detail=str(exc)) from exc
        except HumanSubmissionError as exc:
            raise HTTPException(422, detail=str(exc)) from exc
        except MultiAgentError as exc:
            raise HTTPException(409, detail=str(exc)) from exc

    return app
