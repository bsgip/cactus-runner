from datetime import datetime
from cactus_runner.models import StepInfo, StepStatus


def test_step_info():

    step = StepInfo()
    assert step.get_step_status() == StepStatus.PENDING  # No dates set

    step.started_at = datetime.now()
    assert step.get_step_status() == StepStatus.ACTIVE  # Started but not completed

    step.completed_at = datetime.now()
    assert step.get_step_status() == StepStatus.RESOLVED  # Both dates set
