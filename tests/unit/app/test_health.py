import pytest

from cactus_runner.app.database import remove_database_connection
from cactus_runner.app.health import is_healthy


@pytest.mark.anyio
async def test_is_healthy_no_db():
    remove_database_connection()
    result = await is_healthy()
    assert result is False


@pytest.mark.anyio
async def test_is_healthy_with_empty_db(pg_empty_config):
    result = await is_healthy()
    assert result is True


@pytest.mark.anyio
async def test_is_healthy_with_full_db(pg_base_config):
    result = await is_healthy()
    assert result is True
