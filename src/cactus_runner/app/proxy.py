import http
from dataclasses import dataclass, field
from datetime import UTC, datetime

from aiohttp import client, web
from multidict import CIMultiDict

from cactus_runner.models import ActiveTestProcedure, ForcedResponseStatusOverride


@dataclass
class ProxyResult:
    """The result of proxying a request. What was sent and the raw response"""

    uri: str
    request_method: str
    request_body: bytes
    request_encoding: str | None
    request_headers: CIMultiDict[str]

    response: web.Response
    response_timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


async def do_proxy(method: str, headers: CIMultiDict[str], remote_url: str, request_body: bytes) -> web.Response:
    async with client.request(
        method, remote_url, headers=headers, allow_redirects=False, data=request_body
    ) as response:
        return web.Response(
            headers=response.headers.copy(), status=http.HTTPStatus(response.status), body=await response.read()
        )


def find_active_forced_response_status_override(
    relative_url: str, overrides: list[ForcedResponseStatusOverride]
) -> ForcedResponseStatusOverride | None:
    """Finds the still active override matching relative_url, if any"""
    now = datetime.now(UTC)
    for override in overrides:
        if override.route != relative_url:
            continue
        if override.expires_at is not None and now >= override.expires_at:
            return None  # This override has lapsed - fall through to being served normally
        return override
    return None


async def proxy_request(
    request: web.Request, remote_url: str, relative_url: str, active_test_procedure: ActiveTestProcedure
) -> ProxyResult:
    request_body = await request.read()
    request_headers = request.headers.copy()
    request_method = request.method

    forced_override = find_active_forced_response_status_override(
        relative_url, active_test_procedure.forced_response_status_overrides
    )

    # Forward the request to the reference server
    if forced_override is not None:
        # Intercept before ever reaching the reference server and return the forced status instead
        response_headers = CIMultiDict()
        if forced_override.retry_after_seconds is not None:
            response_headers["Retry-After"] = str(forced_override.retry_after_seconds)
        response = web.Response(status=http.HTTPStatus(forced_override.status), headers=response_headers)
    elif active_test_procedure.communications_disabled:
        # We simulate communication loss as a series of HTTP 500 responses
        response = web.Response(status=http.HTTPStatus.INTERNAL_SERVER_ERROR, body="COMMS DISABLED")
    else:
        response = await do_proxy(request_method, request_headers, remote_url, request_body)

    return ProxyResult(
        uri=remote_url,
        request_method=request.method,
        request_body=request_body,
        request_encoding=request.charset,
        request_headers=request_headers,
        response=response,
        response_timestamp=datetime.now(UTC),
    )
