"""Shared error contract for service and HTTP layers."""
import re

from lzo.contracts import JsonObject


class ApiError(Exception):
    def __init__(self, code: str, status: int, message: str, details: JsonObject | None = None):
        super().__init__(code)
        self.code = code
        self.status = status
        self.message = message
        self.details = details


class Conflict(ApiError):
    def __init__(self, code: str, message: str, details: JsonObject | None = None):
        super().__init__(code, 409, message, details)


class Forbidden(ApiError):
    def __init__(self, code: str, message: str, details: JsonObject | None = None):
        super().__init__(code, 403, message, details)


def parse_if_match(value: str | None) -> int:
    if value is None:
        raise ApiError("PRECONDITION_REQUIRED", 428, "If-Match revision is required")
    if not re.fullmatch(r'"(0|[1-9][0-9]*)"', value):
        raise ApiError("INVALID_IF_MATCH", 422, "If-Match must be a quoted nonnegative revision")
    return int(value[1:-1])


def require_idempotency_key(value: str | None) -> str:
    if value is None:
        raise ApiError("PRECONDITION_REQUIRED", 428, "Idempotency-Key is required")
    if not value or len(value) > 256:
        raise ApiError("INVALID_IDEMPOTENCY_KEY", 422, "Idempotency-Key must have 1 to 256 characters")
    return value
