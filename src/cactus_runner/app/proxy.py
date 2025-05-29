import http

from aiohttp import client, web

from cactus_runner.models import ActiveTestProcedure


async def proxy_request(
    request: web.Request, remote_url: str, active_test_procedure: ActiveTestProcedure
) -> web.Response:

    # Forward the request to the reference server
    if active_test_procedure.communications_disabled:
        # We simulate communication loss as a series of HTTP 500 responses
        return web.Response(status=http.HTTPStatus.INTERNAL_SERVER_ERROR, body="COMMS DISABLED")
    else:
        async with client.request(
            request.method, remote_url, headers=request.headers.copy(), allow_redirects=False, data=await request.read()
        ) as response:
            headers = response.headers.copy()
            return web.Response(headers=headers, status=http.HTTPStatus(response.status), body=await response.read())
