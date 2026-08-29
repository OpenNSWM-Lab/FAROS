"""Lightweight LLM task scheduler.

The scheduler does not reduce scientific checks. It only centralizes bounded
concurrency and retry behavior for transient provider pressure.
"""

from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Callable, Generic, Iterable, Optional, TypeVar

from app.core.user_context import call_with_current_context

T = TypeVar("T")


def _default_max_inflight() -> int:
    try:
        configured = int(os.getenv("FAROS_LLM_MAX_INFLIGHT", "4"))
    except ValueError:
        configured = 4
    return max(1, min(16, configured))


def _is_rate_limit_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in ["429", "rate limit", "too many requests"])


class LLMTaskScheduler(Generic[T]):
    """Bounded execution wrapper for individual LLM calls."""

    def __init__(
        self,
        *,
        max_inflight: Optional[int] = None,
        retry_delays: Optional[Iterable[float]] = None,
    ):
        self.max_inflight = max_inflight or _default_max_inflight()
        self.retry_delays = list(retry_delays) if retry_delays is not None else [2.0, 4.0, 8.0]
        self._semaphore = threading.BoundedSemaphore(self.max_inflight)

    def _run_once(self, fn: Callable[[], T], timeout_seconds: Optional[float]) -> T:
        if timeout_seconds is None or timeout_seconds <= 0:
            return fn()
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="llm-task-timeout")
        future = executor.submit(call_with_current_context(fn))
        try:
            return future.result(timeout=timeout_seconds)
        except FutureTimeoutError as exc:
            future.cancel()
            raise TimeoutError(f"LLM task exceeded timeout of {timeout_seconds:.1f}s") from exc
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    def run(
        self,
        task_type: str,
        fn: Callable[[], T],
        *,
        timeout_seconds: Optional[float] = None,
    ) -> T:
        """Run one LLM task with global-style bounds and 429 backoff."""

        del task_type  # reserved for future per-task metrics and priorities
        with self._semaphore:
            attempt = 0
            while True:
                try:
                    return self._run_once(fn, timeout_seconds)
                except Exception as exc:
                    if not _is_rate_limit_error(exc) or attempt >= len(self.retry_delays):
                        raise
                    delay = max(0.0, float(self.retry_delays[attempt]))
                    attempt += 1
                    if delay:
                        time.sleep(delay)


_default_scheduler: Optional[LLMTaskScheduler] = None
_scheduler_lock = threading.Lock()


def get_llm_task_scheduler() -> LLMTaskScheduler:
    global _default_scheduler
    if _default_scheduler is None:
        with _scheduler_lock:
            if _default_scheduler is None:
                _default_scheduler = LLMTaskScheduler()
    return _default_scheduler
