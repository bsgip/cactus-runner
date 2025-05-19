from datetime import datetime, timezone
from http import HTTPStatus
from unittest.mock import AsyncMock, MagicMock

import pytest
from assertical.fake.generator import generate_class_instance
from envoy_schema.admin.schema.config import (
    RuntimeServerConfigRequest,
    RuntimeServerConfigResponse,
)
from envoy_schema.admin.schema.site import SiteResponse
from envoy_schema.admin.schema.site_control import (
    SiteControlGroupRequest,
    SiteControlGroupResponse,
    SiteControlRequest,
)
from envoy_schema.admin.schema.uri import (
    ServerConfigRuntimeUri,
    SiteControlGroupUri,
    SiteControlUri,
)

from cactus_runner.app.envoy_admin_client import (
    EnvoyAdminClient,
    EnvoyAdminClientAuthParams,
)


@pytest.fixture
def mock_session_with_json_response():
    def _mock(json_data: dict = None, status: int = 200, method: str = "get", location_header: str | None = None):
        mock_response = MagicMock()
        mock_response.status = status
        mock_response.json = AsyncMock(return_value=json_data or {})
        mock_response.raise_for_status = MagicMock()
        mock_session = MagicMock()

        if location_header is not None:
            mock_response.headers = {"Location": location_header}

        if method == "get":
            mock_session.get.return_value.__aenter__.return_value = mock_response
        elif method == "post":
            mock_session.post = AsyncMock(return_value=mock_response)
        elif method == "delete":
            mock_session.delete = AsyncMock(return_value=mock_response)
        return mock_session, mock_response

    return _mock


@pytest.mark.asyncio
async def test_get_single_site(mock_session_with_json_response):
    # Arrange
    expected_json = generate_class_instance(SiteResponse, seed=123).model_dump()
    mock_session, _ = mock_session_with_json_response(expected_json, method="get")

    client = EnvoyAdminClient("http://localhost", EnvoyAdminClientAuthParams("user", "pass"))
    client._session = mock_session

    # Act
    site = await client.get_single_site(site_id=123)

    # Assert
    assert isinstance(site, SiteResponse)
    assert site.site_id == expected_json["site_id"]
    mock_session.get.assert_called_once()


@pytest.mark.asyncio
async def test_delete_single_site(mock_session_with_json_response):
    mock_session, _ = mock_session_with_json_response(status=204, method="delete")

    client = EnvoyAdminClient("http://localhost", EnvoyAdminClientAuthParams("user", "pass"))
    client._session = mock_session

    status = await client.delete_single_site(site_id=456)

    assert status == HTTPStatus.NO_CONTENT
    mock_session.delete.assert_called_once()


@pytest.mark.asyncio
async def test_get_site_control_group(mock_session_with_json_response):
    # Arrange
    expected_json = generate_class_instance(SiteControlGroupResponse, seed=123).model_dump()
    mock_session, _ = mock_session_with_json_response(expected_json, method="get")

    client = EnvoyAdminClient("http://localhost", EnvoyAdminClientAuthParams("user", "pass"))
    client._session = mock_session

    # Act
    group_response = await client.get_site_control_group(12345)

    # Assert
    assert isinstance(group_response, SiteControlGroupResponse)
    assert group_response.site_control_group_id == expected_json["site_control_group_id"]
    mock_session.get.assert_called_once()


@pytest.mark.asyncio
async def test_delete_site_controls_in_range(mock_session_with_json_response):
    mock_session, _ = mock_session_with_json_response(status=204, method="delete")

    client = EnvoyAdminClient("http://localhost", EnvoyAdminClientAuthParams("user", "pass"))
    client._session = mock_session

    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end = datetime(2024, 1, 2, tzinfo=timezone.utc)

    status = await client.delete_site_controls_in_range(group_id=1, period_start=start, period_end=end)

    assert status == HTTPStatus.NO_CONTENT
    mock_session.delete.assert_called_once()


@pytest.mark.asyncio
async def test_close_session():
    mock_session = MagicMock()
    mock_session.close = AsyncMock()

    client = EnvoyAdminClient("http://localhost", EnvoyAdminClientAuthParams("user", "pass"))
    client._session = mock_session

    await client.close_session()

    mock_session.close.assert_called_once()


@pytest.mark.asyncio
async def test_update_runtime_config(mock_session_with_json_response):
    # Arrange
    request_data = generate_class_instance(RuntimeServerConfigRequest, seed=123).model_dump()
    config_request = MagicMock()
    config_request.model_dump.return_value = request_data

    mock_session, mock_response = mock_session_with_json_response(status=200, method="post")

    client = EnvoyAdminClient("http://localhost", EnvoyAdminClientAuthParams("user", "pass"))
    client._session = mock_session

    # Act
    status = await client.update_runtime_config(config_request)

    # Assert
    assert status == HTTPStatus.OK
    mock_session.post.assert_called_once()
    mock_session.post.assert_called_with(ServerConfigRuntimeUri, json=request_data)


@pytest.mark.asyncio
async def test_get_runtime_config(mock_session_with_json_response):
    # Arrange
    expected_json = generate_class_instance(RuntimeServerConfigResponse, seed=123).model_dump()
    mock_session, _ = mock_session_with_json_response(expected_json, method="get")

    client = EnvoyAdminClient("http://localhost", EnvoyAdminClientAuthParams("user", "pass"))
    client._session = mock_session

    # Act
    config = await client.get_runtime_config()

    # Assert
    assert isinstance(config, RuntimeServerConfigResponse)
    assert config.dcap_pollrate_seconds == expected_json["dcap_pollrate_seconds"]
    mock_session.get.assert_called_once_with(ServerConfigRuntimeUri)


@pytest.mark.asyncio
async def test_post_site_control_group(mock_session_with_json_response):
    # Arrange
    mock_session, _ = mock_session_with_json_response(
        status=201, method="post", location_header="/site_control_group/12345"
    )

    client = EnvoyAdminClient("http://localhost", EnvoyAdminClientAuthParams("admin", "pw"))
    client._session = mock_session

    group = generate_class_instance(SiteControlGroupRequest, seed=123)

    # Act
    result = await client.post_site_control_group(group)

    # Assert
    assert result == 12345, "This is the ID extracted from Location header"
    mock_session.post.assert_called_once_with(SiteControlGroupUri, json=group.model_dump())


@pytest.mark.asyncio
async def test_create_site_controls(mock_session_with_json_response):
    # Arrange
    mock_session, _ = mock_session_with_json_response(status=201, method="post")

    client = EnvoyAdminClient("http://localhost", EnvoyAdminClientAuthParams("admin", "pw"))
    client._session = mock_session

    control_1 = generate_class_instance(SiteControlRequest, seed=123)
    control_2 = generate_class_instance(SiteControlRequest, seed=345)

    # Act
    status = await client.create_site_controls(group_id=42, control_list=[control_1, control_2])

    # Assert
    assert status == HTTPStatus.CREATED
    mock_session.post.assert_called_once_with(
        SiteControlUri.format(group_id=42), json=[control_1.model_dump(), control_2.model_dump()]
    )
