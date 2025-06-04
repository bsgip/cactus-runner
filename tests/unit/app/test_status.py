from unittest.mock import Mock

import pytest
from assertical.fake.sqlalchemy import assert_mock_session, create_mock_session
from cactus_test_definitions.checks import Check

from cactus_runner.app import status
from cactus_runner.app.check import CheckResult
from cactus_runner.models import (
    ClientInteraction,
    CriteriaEntry,
    RunnerStatus,
    StepStatus,
)


@pytest.mark.parametrize(
    "step_status,expected",
    [
        ({}, "0/0 steps complete."),
        ({"step_name": StepStatus.PENDING}, "0/1 steps complete."),
        ({"step_name": StepStatus.RESOLVED}, "1/1 steps complete."),
        (
            {"step_1": StepStatus.RESOLVED, "step_2": StepStatus.RESOLVED, "step_3": StepStatus.PENDING},
            "2/3 steps complete.",
        ),
    ],
)
def test_get_runner_status_summary(step_status, expected):
    assert status.get_runner_status_summary(step_status=step_status) == expected


@pytest.mark.anyio
async def test_get_active_runner_status(mocker):
    # Arrange
    mock_session = create_mock_session()
    mock_run_check = mocker.patch("cactus_runner.app.status.run_check")

    expected_test_name = "TEST_NAME"
    expected_step_status = {"step_name": StepStatus.PENDING}
    expected_status_summary = "0/1 steps complete."
    active_test_procedure = Mock()
    active_test_procedure.name = expected_test_name
    active_test_procedure.step_status = expected_step_status
    active_test_procedure.definition = Mock()
    active_test_procedure.definition.criteria = Mock()
    criteria_check = Check("check-1", {})
    active_test_procedure.definition.criteria.checks = [criteria_check]
    mock_run_check.return_value = CheckResult(True, "Details on Check 1")

    request_history = Mock()
    last_client_interaction = Mock()

    # Act
    runner_status = await status.get_active_runner_status(
        session=mock_session,
        active_test_procedure=active_test_procedure,
        request_history=request_history,
        last_client_interaction=last_client_interaction,
    )

    # Assert
    assert isinstance(runner_status, RunnerStatus)
    assert runner_status.last_client_interaction == last_client_interaction
    assert runner_status.request_history == request_history
    assert runner_status.test_procedure_name == expected_test_name
    assert runner_status.step_status == expected_step_status
    assert runner_status.status_summary == expected_status_summary
    assert runner_status.criteria == [CriteriaEntry(True, "check-1", "Details on Check 1")]
    assert_mock_session(mock_session)


@pytest.mark.anyio
async def test_get_active_runner_status_calls_get_runner_status_summary(mocker):
    get_runner_status_summary_spy = mocker.spy(status, "get_runner_status_summary")

    mock_session = create_mock_session()
    expected_step_status = {"step_name": StepStatus.PENDING}
    active_test_procedure = Mock()
    active_test_procedure.step_status = expected_step_status
    active_test_procedure.definition = Mock()
    active_test_procedure.definition.criteria = None
    request_history = Mock()
    last_client_interaction = Mock()

    _ = await status.get_active_runner_status(
        session=mock_session,
        active_test_procedure=active_test_procedure,
        request_history=request_history,
        last_client_interaction=last_client_interaction,
    )
    get_runner_status_summary_spy.assert_called_once_with(step_status=expected_step_status)
    assert_mock_session(mock_session)


def test_get_runner_status(example_client_interaction: ClientInteraction):
    runner_status = status.get_runner_status(last_client_interaction=example_client_interaction)

    assert isinstance(runner_status, RunnerStatus)
    assert runner_status.status_summary == "No test procedure running"
    assert runner_status.last_client_interaction == example_client_interaction
    assert runner_status.test_procedure_name == "-"
    assert runner_status.step_status is None
    assert runner_status.request_history == []
