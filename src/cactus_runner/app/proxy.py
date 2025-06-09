import http
from dataclasses import dataclass

from aiohttp import client, web
from multidict import CIMultiDict

from cactus_runner.models import ActiveTestProcedure


@dataclass
class ProxyResult:
    """The result of proxying a request. What was sent and the raw response"""

    uri: str
    request_method: str
    request_body: bytes
    request_encoding: str | None
    request_headers: CIMultiDict[str]

    response: web.Response


async def do_proxy(method: str, headers: CIMultiDict[str], remote_url: str, request_body: bytes) -> web.Response:
    async with client.request(
        method, remote_url, headers=headers, allow_redirects=False, data=request_body
    ) as response:
        return web.Response(
            headers=response.headers.copy(), status=http.HTTPStatus(response.status), body=await response.read()
        )


async def proxy_request(
    request: web.Request, remote_url: str, active_test_procedure: ActiveTestProcedure
) -> ProxyResult:
    request_body = await request.read()
    request_headers = request.headers.copy()
    request_method = request.method

    # Forward the request to the reference server
    if active_test_procedure.communications_disabled:
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
    )
