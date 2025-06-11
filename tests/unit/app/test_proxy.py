import http
from unittest.mock import ANY, AsyncMock, MagicMock

import pytest

from cactus_runner.app import proxy
from cactus_runner.models import ActiveTestProcedure


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
    remote_url = request.path

    # Act
    proxy_result = await proxy.proxy_request(
        request=request, remote_url=remote_url, active_test_procedure=active_test_procedure
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
    active_test_procedure.is_finished.return_value = False
    remote_url = ""

    # Act
    proxy_result = await proxy.proxy_request(
        request=request, remote_url=remote_url, active_test_procedure=active_test_procedure
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
