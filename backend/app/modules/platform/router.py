"""Platform router aggregating shared infrastructure endpoints."""

from fastapi import APIRouter

from app.modules.platform.artifacts_api import router as artifacts_router
from app.modules.platform.experiments_api import router as experiments_router
from app.modules.platform.plan_links_api import router as plan_links_router
from app.modules.platform.plan_packages_api import router as plan_packages_router
from app.modules.platform.plan_sessions_api import router as plan_sessions_router
from app.modules.platform.providers_api import router as providers_router
from app.modules.platform.research_plans_api import router as research_plans_router
from app.modules.platform.runs_api import router as runs_router
from app.modules.platform.runs_monitor_api import router as runs_monitor_router
from app.modules.platform.skills_api import router as skills_router
from app.modules.platform.templates_api import router as templates_router

router = APIRouter(tags=["module:platform"])
router.include_router(research_plans_router)
router.include_router(plan_packages_router)
router.include_router(plan_sessions_router)
router.include_router(plan_links_router)
router.include_router(runs_router)
router.include_router(runs_monitor_router)
router.include_router(experiments_router)
router.include_router(artifacts_router)
router.include_router(providers_router)
router.include_router(skills_router)
router.include_router(templates_router)
