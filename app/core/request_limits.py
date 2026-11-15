"""Limit bytes delivered to the extraction route before multipart parsing."""

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class _RequestBodyTooLarge(BaseException):
    """Unwind the parser without FastAPI turning the failure into HTTP 400.

    Starlette closes multipart temporary files when stream reading raises a
    BaseException. Only this middleware catches this private control signal;
    cancellations and all other errors continue to propagate normally.
    """


def _route_path(scope: Scope) -> str:
    """Account for a mount or proxy root path in the same way as routing."""
    path = scope["path"]
    root = scope.get("root_path", "").rstrip("/")
    if root and (path == root or path.startswith(root + "/")):
        return path[len(root):]
    return path


def _declared_length_status(scope: Scope, limit: int) -> int | None:
    """Reject malformed/duplicate lengths and lengths exceeding the limit."""
    values = [value for key, value in scope.get("headers", []) if key.lower() == b"content-length"]
    if not values:
        return None
    if len(values) != 1 or not values[0] or not all(48 <= char <= 57 for char in values[0]):
        return 400
    # Avoid converting an attacker-controlled, potentially huge decimal to int.
    length = values[0].lstrip(b"0") or b"0"
    maximum = str(limit).encode("ascii")
    if len(length) > len(maximum) or (len(length) == len(maximum) and length > maximum):
        return 413
    return None


class ExtractionBodyLimitMiddleware:
    """Bound the complete body consumed by POST /api/extract, per request.

    Content-Length permits early rejection but is never trusted as the actual
    byte count. The app must consume the body before starting its response: a
    response already sent cannot be replaced if a later read exceeds the limit.
    """

    def __init__(self, app: ASGIApp, max_request_bytes: int) -> None:
        if max_request_bytes <= 0:
            raise ValueError("max_request_bytes must be positive")
        self.app = app
        self.max_request_bytes = max_request_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST" or _route_path(scope) != "/api/extract":
            await self.app(scope, receive, send)
            return

        status = _declared_length_status(scope, self.max_request_bytes)
        if status is not None:
            detail = "Invalid Content-Length" if status == 400 else "Request body too large"
            await JSONResponse({"detail": detail}, status_code=status)(scope, receive, send)
            return

        size = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal size
            message = await receive()
            if message["type"] == "http.request":
                size += len(message.get("body", b""))
                if size > self.max_request_bytes:
                    raise _RequestBodyTooLarge()
            return message

        async def tracked_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except _RequestBodyTooLarge:
            if response_started:
                # Abort instead of sending a second, invalid response.
                raise
            await JSONResponse({"detail": "Request body too large"}, status_code=413)(scope, receive, send)
