"""Code module router.

Aggregates all code-generation and code-execution endpoints behind a single
module entrypoint.
"""

from fastapi import APIRouter

from app.modules.code.code_context_api import router as code_context_router
from app.modules.code.code_eval_api import router as code_eval_router
from app.modules.code.code_jobs_api import router as code_jobs_router
from app.modules.code.code_projects_api import router as code_projects_router
from app.modules.code.code_sessions_api import router as code_sessions_router
from app.modules.code.codegen_sessions_api import router as codegen_sessions_router
from app.modules.code.code_agent_api import router as code_agent_router
from app.modules.code.code_blueprint_api import router as code_blueprint_router

router = APIRouter(tags=["module:code"])
router.include_router(code_context_router)
router.include_router(code_eval_router)
router.include_router(code_jobs_router)
router.include_router(code_projects_router)
router.include_router(code_sessions_router)
router.include_router(codegen_sessions_router)
router.include_router(code_agent_router)
router.include_router(code_blueprint_router)
