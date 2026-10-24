"""Health checks. live = the process is up. ready = it can serve requests.
Gemini is not called here (it costs quota); we only report whether it is configured."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from insureassist.api.deps import ServicesDep
from insureassist.config import settings

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
def ready(services: ServicesDep) -> JSONResponse:
    checks: dict[str, str] = {}
    try:
        with services.session_factory() as session:
            session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:  # any DB problem -> not ready (details go to the log, not here)
        checks["database"] = "error"
    try:
        checks["vector_store"] = f"ok ({services.store.backend}, {services.store.count} chunks)"
    except Exception:
        checks["vector_store"] = "error"
    checks["embedding_model"] = "ok"
    configured = settings.llm_provider == "huggingface" or bool(
        settings.gemini_api_key and settings.gemini_api_key.get_secret_value()
    )
    checks["llm"] = "configured" if configured else "not configured"
    ok = all(not v.startswith("error") for v in checks.values())
    return JSONResponse(
        {"status": "ready" if ok else "not_ready", "checks": checks},
        status_code=200 if ok else 503,
    )
