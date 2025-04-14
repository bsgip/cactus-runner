import http
from datetime import datetime, timezone

import pytest

from cactus_runner.models import (
    ClientInteraction,
    ClientInteractionType,
    RequestEntry,
    RunnerStatus,
    StepStatus,
)


@pytest.fixture
def runner_status_fixture():
    return RunnerStatus(
        status_summary="status summery here",
        last_client_interaction=ClientInteraction(
            interaction_type=ClientInteractionType.PROXIED_REQUEST, timestamp=datetime.now(timezone.utc)
        ),
        test_procedure_name="ALL-01",
        step_status={
            "ALL-01-001": StepStatus.RESOLVED,
            "ALL-01-002": StepStatus.PENDING,
            "ALL-01-003": StepStatus.PENDING,
            "ALL-01-004": StepStatus.PENDING,
        },
        request_history=[
            RequestEntry(
                url="http://localhost:8000/dcap",
                path="/dcap",
                status=http.HTTPStatus.OK,
                timestamp=datetime.now(timezone.utc),
                step_name="ALL-01-001",
            )
        ],
    )
