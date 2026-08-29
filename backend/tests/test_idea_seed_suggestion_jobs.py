import asyncio
import time

import pytest
from fastapi import HTTPException

from app.core.user_context import use_user
from app.modules.idea import ideas_api


def test_seed_suggestion_job_completes_and_is_user_scoped(monkeypatch):
    expected = ideas_api.SeedSuggestionResponse(
        providerName="qwen",
        model="qwen-plus",
        suggestions=[
            ideas_api.SeedSuggestionItem(
                titleZh="科学主张核验",
                titleEn="Scientific claim verification",
                query="Citation-aware retrieval for scientific claim verification evaluated by claim-level F1 score",
            ),
            ideas_api.SeedSuggestionItem(
                titleZh="可追溯科研问答",
                titleEn="Traceable scientific question answering",
                query="Evidence-grounded scientific question answering evaluated by citation precision and answer correctness",
            ),
        ],
    )
    monkeypatch.setattr(
        ideas_api,
        "_request_qwen_seed_suggestions",
        lambda **_kwargs: expected,
    )

    with ideas_api._seed_suggestion_jobs_lock:
        ideas_api._seed_suggestion_jobs.clear()

    with use_user("alice"):
        created = asyncio.run(
            ideas_api.create_seed_suggestion_job(
                ideas_api.SeedSuggestionRequest(userIdea="trustworthy AI science")
            )
        )

    deadline = time.monotonic() + 1
    current = None
    while time.monotonic() < deadline:
        with use_user("alice"):
            current = asyncio.run(ideas_api.get_seed_suggestion_job(created.jobId))
        if current.status == "completed":
            break
        time.sleep(0.01)

    assert current is not None
    assert current.status == "completed"
    assert current.result == expected

    with use_user("bob"), pytest.raises(HTTPException) as exc_info:
        asyncio.run(ideas_api.get_seed_suggestion_job(created.jobId))
    assert exc_info.value.status_code == 404
