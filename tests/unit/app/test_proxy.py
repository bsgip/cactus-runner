import http
from unittest.mock import ANY, AsyncMock, MagicMock

import pytest
from aiohttp.web import Response

from cactus_runner.app import proxy
from cactus_runner.models import ActiveTestProcedure


@pytest.mark.asyncio
async def test_proxy_request(pg_empty_config, mocker):
    # Arrange
    request_data = ""
    request_read = AsyncMock()
    request_read.return_value = request_data
    request = MagicMock()
    request.path = "/dcap"
    request.path_qs = "/dcap"
    request.method = "GET"
    request.read = request_read

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
    response = await proxy.proxy_request(
        request=request, remote_url=remote_url, active_test_procedure=active_test_procedure
    )

    # Assert
    mock_client_request.assert_called_once_with(
        request.method, request.path_qs, headers=ANY, allow_redirects=False, data=request_data
    )

    #  ... verify we received the expected proxied response
    #      i.e. the one supplied to 'mock_client_request'
    assert isinstance(response, Response)
    assert response.status == response_status
    assert response.text == response_text
    for key, value in response_headers.items():
        assert key in response.headers
        assert response.headers[key] == value


@pytest.mark.asyncio
async def test_proxy_request_disables_communications(pg_empty_config, mocker):
    # Arrange
    request = MagicMock()
    mock_client_request = mocker.patch("aiohttp.client.request")
    active_test_procedure = MagicMock()
    active_test_procedure.communications_disabled = True
    remote_url = ""

    # Act
    response = await proxy.proxy_request(
        request=request, remote_url=remote_url, active_test_procedure=active_test_procedure
    )

    # Assert
    assert mock_client_request.call_count == 0
    assert response.status == http.HTTPStatus.INTERNAL_SERVER_ERROR


def test_communications_disabled_defaults_false():
    assert ActiveTestProcedure.communications_disabled is False
