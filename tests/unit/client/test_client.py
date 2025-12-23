from datetime import datetime, timezone
from http import HTTPStatus
from unittest.mock import MagicMock, Mock

import pytest
from aiohttp import ConnectionTimeoutError
from cactus_schema.runner import (
    ClientInteraction,
    InitResponseBody,
    RunnerStatus,
    StartResponseBody,
)

from cactus_runner.client import RunnerClient, RunnerClientException


@pytest.mark.asyncio
async def test_initialise():
    # Arrange
    expected_init_result = InitResponseBody(
        status="PLACEHOLDER-STATUS",
        test_procedure="ALL-01",
        timestamp=datetime.now(timezone.utc),
        is_started=False,
    )
    run_request = MagicMock()
    run_request.to_json = Mock(return_value="Dummy Run Request JSON")
    mock_session = MagicMock()
    mock_session.post.return_value.__aenter__.return_value.status = 200
    mock_session.post.return_value.__aenter__.return_value.text.return_value = expected_init_result.to_json()

    # Act
    init_result = await RunnerClient.initialise(session=mock_session, run_request=run_request)

    # Assert
    assert mock_session.post.return_value.__aenter__.return_value.text.call_count == 1
    assert isinstance(init_result, InitResponseBody)
    assert init_result == expected_init_result


@pytest.mark.asyncio
async def test_initialise_connectionerror():
    # Arrange
    mock_session = MagicMock()
    mock_session.post.side_effect = ConnectionTimeoutError

    run_request = MagicMock()
    run_request.to_json = Mock(return_value="Dummy Run Request JSON")

    # Act/Assert
    with pytest.raises(RunnerClientException, match="Unexpected failure while initialising test."):
        _ = await RunnerClient.initialise(
            session=mock_session,
            run_request=run_request,
        )


@pytest.mark.asyncio
async def test_start():
    # Arrange
    expected_start_result = StartResponseBody(
        status="PLACEHOLDER-STATUS", test_procedure="ALL-01", timestamp=datetime.now(timezone.utc)
    )
    mock_session = MagicMock()
    mock_session.post.return_value.__aenter__.return_value.status = 200
    mock_session.post.return_value.__aenter__.return_value.text.return_value = expected_start_result.to_json()

    # Act
    start_result = await RunnerClient.start(session=mock_session)

    # Assert
    mock_session.post_assert_called_once_with(url="/start")
    assert mock_session.post.return_value.__aenter__.return_value.text.call_count == 1
    assert isinstance(start_result, StartResponseBody)
    assert start_result == expected_start_result


@pytest.mark.asyncio
async def test_start_precondition_failures():
    # Arrange
    expected_error_message = "This is an error message returned from the underlying runner"
    mock_session = MagicMock()
    mock_session.post.return_value.__aenter__.return_value.status = HTTPStatus.PRECONDITION_FAILED
    mock_session.post.return_value.__aenter__.return_value.text.return_value = expected_error_message

    # Act
    with pytest.raises(RunnerClientException) as exc_info:
        await RunnerClient.start(session=mock_session)

    # Assert
    assert exc_info.value.error_message == expected_error_message
    assert exc_info.value.http_status_code == HTTPStatus.PRECONDITION_FAILED


@pytest.mark.asyncio
async def test_start_connectionerror():
    # Arrange
    mock_session = MagicMock()
    mock_session.post.side_effect = ConnectionTimeoutError

    # Act/Assert
    with pytest.raises(RunnerClientException, match="Unexpected failure while starting test."):
        _ = await RunnerClient.start(session=mock_session)


@pytest.mark.asyncio
async def test_finalize():
    # Arrange
    mock_session = MagicMock()
    mock_session.post.return_value.__aenter__.return_value.status = 200
    mock_session.post.return_value.__aenter__.return_value.read.return_value = bytes()

    # Act
    finalize_result = await RunnerClient.finalize(session=mock_session)

    # Assert
    mock_session.post.assert_called_once_with(url="/finalize")
    assert mock_session.post.return_value.__aenter__.return_value.read.call_count == 1
    assert isinstance(finalize_result, bytes)


@pytest.mark.asyncio
async def test_finalize_connectionerror():
    # Arrange
    mock_session = MagicMock()
    mock_session.post.side_effect = ConnectionTimeoutError

    # Act/Assert
    with pytest.raises(RunnerClientException, match="Unexpected failure while finalizing test procedure."):
        _ = await RunnerClient.finalize(session=mock_session)


@pytest.mark.asyncio
async def test_status(runner_status_fixture):
    # Arrange
    expected_status = runner_status_fixture
    mock_session = MagicMock()
    mock_session.get.return_value.__aenter__.return_value.status = 200
    mock_session.get.return_value.__aenter__.return_value.text.return_value = expected_status.to_json()

    # Act
    status = await RunnerClient.status(session=mock_session)

    # Assert
    mock_session.get.assert_called_once_with(url="/status")
    assert mock_session.get.return_value.__aenter__.return_value.text.call_count == 1
    assert isinstance(status, RunnerStatus)
    assert status == expected_status


@pytest.mark.asyncio
async def test_status_connectionerror():
    # Arrange
    mock_session = MagicMock()
    mock_session.get.side_effect = ConnectionTimeoutError

    # Act/Assert
    with pytest.raises(RunnerClientException, match="Unexpected failure while requesting test procedure status."):
        _ = await RunnerClient.status(session=mock_session)


@pytest.mark.asyncio
async def test_last_interaction(runner_status_fixture):
    # Arrange
    expected_last_interaction = runner_status_fixture.last_client_interaction
    mock_session = MagicMock()
    mock_session.get.return_value.__aenter__.return_value.status = 200
    mock_session.get.return_value.__aenter__.return_value.text.return_value = runner_status_fixture.to_json()

    # Act
    last_interaction = await RunnerClient.last_interaction(session=mock_session)

    # Assert
    mock_session.get.assert_called_once_with(url="/status")
    assert mock_session.get.return_value.__aenter__.return_value.text.call_count == 1
    assert isinstance(last_interaction, ClientInteraction)
    assert last_interaction == expected_last_interaction


@pytest.mark.asyncio
async def test_last_interaction_connectionerror():
    # Arrange
    mock_session = MagicMock()
    mock_session.get.side_effect = ConnectionTimeoutError

    # Act/Assert
    with pytest.raises(RunnerClientException, match="Unexpected failure while requesting test procedure status."):
        _ = await RunnerClient.last_interaction(session=mock_session)
