from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FarosError(ValueError):
    message: str
    status_code: int = 400
    error_code: str = 'faros_error'
    category: str = 'runtime'

    def __str__(self) -> str:
        return self.message


class FarosValidationError(FarosError):
    status_code = 400
    error_code = 'validation_error'
    category = 'validation'

    def __init__(self, message: str, *, error_code: str = 'validation_error', category: str = 'validation'):
        super().__init__(message=message, status_code=400, error_code=error_code, category=category)


class FarosConfigurationError(FarosError):
    def __init__(self, message: str):
        super().__init__(message=message, status_code=400, error_code='configuration_error', category='configuration')


class FarosNotFoundError(FarosError):
    def __init__(self, message: str):
        super().__init__(message=message, status_code=404, error_code='not_found', category='not_found')


class FarosTrustError(FarosError):
    def __init__(self, message: str):
        super().__init__(message=message, status_code=403, error_code='trust_error', category='trust')


class FarosConflictError(FarosError):
    def __init__(self, message: str, *, error_code: str = 'conflict', category: str = 'conflict'):
        super().__init__(message=message, status_code=409, error_code=error_code, category=category)


class FarosStateTransitionError(FarosConflictError):
    def __init__(self, message: str):
        super().__init__(message=message, error_code='state_transition_error', category='runtime_state')


class FarosBlockedError(FarosConflictError):
    def __init__(self, message: str):
        super().__init__(message=message, error_code='run_blocked', category='runtime_state')


class FarosVerificationError(FarosError):
    def __init__(self, message: str):
        super().__init__(message=message, status_code=422, error_code='verification_failed', category='verification')


class FarosPreflightError(FarosError):
    def __init__(self, message: str):
        super().__init__(message=message, status_code=422, error_code='preflight_failed', category='preflight')


class FarosProviderError(FarosError):
    def __init__(self, message: str, *, error_code: str = 'provider_error'):
        super().__init__(message=message, status_code=502, error_code=error_code, category='provider')


class FarosProviderTimeoutError(FarosError):
    def __init__(self, message: str):
        super().__init__(message=message, status_code=504, error_code='provider_timeout', category='provider')
