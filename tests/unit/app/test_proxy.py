import http
from datetime import UTC, datetime, timedelta
from unittest.mock import ANY, AsyncMock, MagicMock

import pytest

from cactus_runner.app import proxy
from cactus_runner.models import ActiveTestProcedure, ForcedResponseStatusOverride


@pytest.mark.asyncio
async def test_proxy_request(mocker):
    # Arrange
    request_data = bytes([0, 55, 77, 89])
    request_read = AsyncMock()
    request_read.return_value = request_data
    request = MagicMock()
    request.path = "/dcap"
    request.path_qs = "/dcap"
    request.method = "GET"
    request.read = request_read
    request.charset = "UTF-9999"  # Set to a nonsensical value to ensure it's being extracted (and not defaulted)

    response_text = "RESPONSE-TEXT"
    response_status = http.HTTPStatus.OK
    response_headers = {"X-API-Key": "API-KEY"}
    mock_client_request = mocker.patch("aiohttp.client.request")
    mock_client_request.return_value.__aenter__.return_value.status = response_status
    mock_client_request.return_value.__aenter__.return_value.read.return_value = response_text
    mock_client_request.return_value.__aenter__.return_value.headers = response_headers

    active_test_procedure = MagicMock()
    active_test_procedure.communications_disabled = False
    active_test_procedure.forced_response_status_overrides = []
    remote_url = request.path

    # Act
    proxy_result = await proxy.proxy_request(
        request=request, remote_url=remote_url, relative_url=request.path, active_test_procedure=active_test_procedure
    )

    # Assert
    assert isinstance(proxy_result, proxy.ProxyResult)
    assert proxy_result.request_encoding == request.charset
    assert proxy_result.request_method == request.method
    assert proxy_result.request_body == request_data

    mock_client_request.assert_called_once_with(
        request.method, request.path_qs, headers=ANY, allow_redirects=False, data=request_data
    )

    #  ... verify we received the expected proxied response
    #      i.e. the one supplied to 'mock_client_request'
    assert proxy_result.response.status == response_status
    assert proxy_result.response.text == response_text
    for key, value in response_headers.items():
        assert key in proxy_result.response.headers
        assert proxy_result.response.headers[key] == value


@pytest.mark.asyncio
async def test_proxy_request_disables_communications(mocker):
    # Arrange
    request = MagicMock()
    request_data = bytes([0, 44, 77, 89])
    request_read = AsyncMock()
    request_read.return_value = request_data
    request.read = request_read

    mock_client_request = mocker.patch("aiohttp.client.request")
    active_test_procedure = MagicMock()
    active_test_procedure.communications_disabled = True
    active_test_procedure.forced_response_status_overrides = []
    active_test_procedure.is_finished.return_value = False
    remote_url = ""

    # Act
    proxy_result = await proxy.proxy_request(
        request=request, remote_url=remote_url, relative_url="/dcap", active_test_procedure=active_test_procedure
    )

    # Assert
    assert isinstance(proxy_result, proxy.ProxyResult)
    assert proxy_result.request_encoding == request.charset
    assert proxy_result.request_method == request.method
    assert proxy_result.request_body == request_data

    assert mock_client_request.call_count == 0
    assert proxy_result.response.status == http.HTTPStatus.INTERNAL_SERVER_ERROR


def test_communications_disabled_defaults_false():
    assert ActiveTestProcedure.communications_disabled is False


@pytest.mark.asyncio
async def test_proxy_request_forced_response_status_intercepts(mocker):
    """A matching, non-expired override should short circuit the proxy entirely and return the forced status"""
    # Arrange
    request = MagicMock()
    request_data = bytes([1, 2, 3])
    request.read = AsyncMock(return_value=request_data)
    request.method = "GET"

    mock_client_request = mocker.patch("aiohttp.client.request")
    active_test_procedure = MagicMock()
    active_test_procedure.communications_disabled = False
    active_test_procedure.forced_response_status_overrides = [
        ForcedResponseStatusOverride(route="/edev/1/derp/1/derc", status=429, retry_after_seconds=180)
    ]

    # Act
    proxy_result = await proxy.proxy_request(
        request=request,
        remote_url="http://example.com/edev/1/derp/1/derc",
        relative_url="/edev/1/derp/1/derc",
        active_test_procedure=active_test_procedure,
    )

    # Assert
    assert mock_client_request.call_count == 0, "The reference server should never have been contacted"
    assert proxy_result.response.status == http.HTTPStatus.TOO_MANY_REQUESTS
    assert proxy_result.response.headers["Retry-After"] == "180"


@pytest.mark.asyncio
async def test_proxy_request_forced_response_status_ignores_mismatched_route(mocker):
    """An override for a different route should not affect this request"""
    # Arrange
    request = MagicMock()
    request.read = AsyncMock(return_value=b"")
    request.method = "GET"
    request.charset = "utf-8"

    response_status = http.HTTPStatus.OK
    mock_client_request = mocker.patch("aiohttp.client.request")
    mock_client_request.return_value.__aenter__.return_value.status = response_status
    mock_client_request.return_value.__aenter__.return_value.read.return_value = b""
    mock_client_request.return_value.__aenter__.return_value.headers = {}

    active_test_procedure = MagicMock()
    active_test_procedure.communications_disabled = False
    active_test_procedure.forced_response_status_overrides = [
        ForcedResponseStatusOverride(route="/some/other/endpoint", status=429)
    ]

    # Act
    proxy_result = await proxy.proxy_request(
        request=request,
        remote_url="http://example.com/edev/1/derp/1/derc",
        relative_url="/edev/1/derp/1/derc",
        active_test_procedure=active_test_procedure,
    )

    # Assert
    assert mock_client_request.call_count == 1
    assert proxy_result.response.status == response_status


@pytest.mark.asyncio
async def test_proxy_request_forced_response_status_expires(mocker):
    """An override whose expiry has passed should no longer intercept requests"""
    # Arrange
    request = MagicMock()
    request.read = AsyncMock(return_value=b"")
    request.method = "GET"
    request.charset = "utf-8"

    response_status = http.HTTPStatus.OK
    mock_client_request = mocker.patch("aiohttp.client.request")
    mock_client_request.return_value.__aenter__.return_value.status = response_status
    mock_client_request.return_value.__aenter__.return_value.read.return_value = b""
    mock_client_request.return_value.__aenter__.return_value.headers = {}

    active_test_procedure = MagicMock()
    active_test_procedure.communications_disabled = False
    active_test_procedure.forced_response_status_overrides = [
        ForcedResponseStatusOverride(
            route="/edev/1/derp/1/derc", status=429, expires_at=datetime.now(UTC) - timedelta(seconds=1)
        )
    ]

    # Act
    proxy_result = await proxy.proxy_request(
        request=request,
        remote_url="http://example.com/edev/1/derp/1/derc",
        relative_url="/edev/1/derp/1/derc",
        active_test_procedure=active_test_procedure,
    )

    # Assert
    assert mock_client_request.call_count == 1
    assert proxy_result.response.status == response_status


@pytest.mark.parametrize(
    "overrides, relative_url, expected",
    [
        ([], "/dcap", None),
        ([ForcedResponseStatusOverride(route="/dcap", status=503)], "/edev", None),
        (
            [ForcedResponseStatusOverride(route="/dcap", status=503)],
            "/dcap",
            ForcedResponseStatusOverride(route="/dcap", status=503),
        ),
        (
            [
                ForcedResponseStatusOverride(
                    route="/dcap", status=503, expires_at=datetime.now(UTC) - timedelta(seconds=1)
                )
            ],
            "/dcap",
            None,
        ),
        (
            [
                ForcedResponseStatusOverride(
                    route="/dcap", status=503, expires_at=datetime.now(UTC) + timedelta(seconds=60)
                )
            ],
            "/dcap",
            ForcedResponseStatusOverride(
                route="/dcap", status=503, expires_at=datetime.now(UTC) + timedelta(seconds=60)
            ),
        ),
    ],
)
def test_find_active_forced_response_status_override(overrides, relative_url, expected):
    result = proxy.find_active_forced_response_status_override(relative_url, overrides)
    if expected is None:
        assert result is None
    else:
        assert result is not None
        assert result.route == expected.route
        assert result.status == expected.status
