"""Central release metadata for the FAROS backend baseline."""

import os

APP_NAME = "FAROS AutoResearch Runtime Backend"
APP_VERSION = "5.15.2"
SOURCE_REVISION = os.getenv("FAROS_SOURCE_REVISION", "development")
API_VERSION = "v1"
RELEASE_PHASE = "faros-llm"
SERVICE_NAME = "faros-runtime-backend"

CAPABILITIES = [
    "faros_runtime",
    "idea_module",
    "experiment_module",
    "code_module",
    "paper_module",
    "review_module",
    "platform_services",
]
