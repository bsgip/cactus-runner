from dataclasses import dataclass

from cactus_runner.models import (
    ActiveTestProcedure,
    RequestEntry,
    RunnerState,
)
from cactus_runner.plugin.backends.common import RunnerBackend


@dataclass(slots=True)
class ActionContext:
    backend: RunnerBackend
    runner_state: RunnerState

    @property
    def active_test_procedure(self) -> ActiveTestProcedure | None:
        return self.runner_state.active_test_procedure


@dataclass(slots=True)
class CheckContext:
    backend: RunnerBackend
    active_test_procedure: ActiveTestProcedure
    request_history: list[RequestEntry]
