from datetime import datetime, timezone
from http import HTTPStatus
from unittest.mock import MagicMock

import pytest
from aiohttp import ConnectionTimeoutError
from cactus_schema.runner import (
    ClientInteraction,
    InitResponseBody,
    RunGroup,
    RunnerStatus,
    RunRequest,
    StartResponseBody,
    TestCertificates,
    TestConfig,
    TestDefinition,
    TestUser,
)
from cactus_test_definitions.client import TestProcedureId

from cactus_runner.client import RunnerClient, RunnerClientException


def make_run_request(run_id: str = "test-run-123") -> RunRequest:
    """Create a minimal RunRequest for testing."""
    return RunRequest(
        run_id=run_id,
        test_definition=TestDefinition(test_procedure_id=TestProcedureId.ALL_01, yaml_definition="test: yaml"),
        run_group=RunGroup(
            run_group_id="1",
            name="test group",
            csip_aus_version=None,
            test_certificates=TestCertificates(aggregator=None, device=None),
        ),
        test_config=TestConfig(pen=12345, subscription_domain=None, is_static_url=False),
        test_user=TestUser(user_id="user-1", name="Test User"),
    )


@pytest.mark.asyncio
async def test_initialise():
    # Arrange
    expected_init_result = InitResponseBody(
        status="PLACEHOLDER-STATUS",
        test_procedure="ALL-01",
        timestamp=datetime.now(timezone.utc),
        is_started=False,
    )
    run_request = make_run_request()
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

    run_request = make_run_request()

    # Act/Assert
    with pytest.raises(RunnerClientException, match="Unexpected failure while initialising test"):
        _ = await RunnerClient.initialise(
            session=mock_session,
            run_request=run_request,
        )


@pytest.mark.asyncio
async def test_initialise_playlist():
    """Test initialising a playlist of multiple RunRequests."""
    # Arrange
    expected_init_result = InitResponseBody(
        status="PLACEHOLDER-STATUS",
        test_procedure="ALL-01",
        timestamp=datetime.now(timezone.utc),
        is_started=True,
    )
    run_requests = [make_run_request("test-1"), make_run_request("test-2"), make_run_request("test-3")]
    mock_session = MagicMock()
    mock_session.post.return_value.__aenter__.return_value.status = 200
    mock_session.post.return_value.__aenter__.return_value.text.return_value = expected_init_result.to_json()

    # Act
    init_result = await RunnerClient.initialise(session=mock_session, run_request=run_requests)

    # Assert
    assert mock_session.post.return_value.__aenter__.return_value.text.call_count == 1
    assert isinstance(init_result, InitResponseBody)
    assert init_result == expected_init_result
    call_kwargs = mock_session.post.call_args.kwargs
    assert "json" in call_kwargs
    assert isinstance(call_kwargs["json"], list)
    assert len(call_kwargs["json"]) == 3


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
