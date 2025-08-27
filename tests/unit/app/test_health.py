import unittest.mock as mock

import pytest

from cactus_runner.app.database import remove_database_connection
from cactus_runner.app.envoy_admin_client import EnvoyAdminClient
from cactus_runner.app.health import is_admin_api_healthy, is_db_healthy


@pytest.mark.anyio
async def test_is_db_healthy_no_db():
    remove_database_connection()
    result = await is_db_healthy()
    assert result is False


@pytest.mark.anyio
async def test_is_db_healthy_with_empty_db(pg_empty_config):
    result = await is_db_healthy()
    assert result is True


@pytest.mark.anyio
async def test_is_db_healthy_with_full_db(pg_base_config):
    result = await is_db_healthy()
    assert result is True


@pytest.mark.anyio
async def test_is_admin_api_healthy_no_data(envoy_admin_client):
    """Test with the full stack"""
    result = await is_admin_api_healthy(envoy_admin_client)
    assert result is True


@pytest.mark.anyio
async def test_is_admin_api_healthy_with_data(envoy_admin_client, pg_base_config):
    """Test with the full stack and data in the DB"""
    result = await is_admin_api_healthy(envoy_admin_client)
    assert result is True


@pytest.mark.anyio
async def test_is_admin_api_healthy_on_fail():
    """Test that exceptions return False"""

    envoy_admin_client = mock.Mock(spec=EnvoyAdminClient)
    envoy_admin_client.get_aggregators.side_effect = Exception("mock exception")

    result = await is_admin_api_healthy(envoy_admin_client)
    assert result is False
