import asyncio

from cactus_test_definitions.client import TestProcedureId

from cactus_runner.client import (
    ClientSession,
    ClientTimeout,
    RunnerClient,
)
from cactus_runner.models import (
    ClientInteraction,
    RunnerStatus,
)


async def main():
    timeout = ClientTimeout(total=30)
    base_url = "http://localhost:8080/"
    async with ClientSession(base_url=base_url, timeout=timeout) as session:
        status: RunnerStatus = await RunnerClient.status(session=session)
        print(status)

        last_interaction: ClientInteraction = await RunnerClient.last_interaction(session=session)
        print(last_interaction)

        test_id = TestProcedureId.ALL_01
        # await RunnerClient.start(session=session, test_id=test_id, aggregator_certificate=None)
        # await RunnerClient.finalize(session=session)


if __name__ == "__main__":
    asyncio.run(main())
