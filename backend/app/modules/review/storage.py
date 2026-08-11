"""Review-domain storage facade."""

from app.storage.paper_storage import get_paper, list_paper_files, read_paper_file
from app.storage.review_storage import (
    create_improvement_request,
    create_review,
    get_review,
    list_improvement_requests,
    list_reviews,
    update_improvement_request,
    update_review,
)

__all__ = [
    "create_improvement_request",
    "create_review",
    "get_paper",
    "get_review",
    "list_improvement_requests",
    "list_paper_files",
    "list_reviews",
    "read_paper_file",
    "update_improvement_request",
    "update_review",
]
