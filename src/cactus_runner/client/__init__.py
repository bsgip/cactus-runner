import logging

from aiohttp import ClientSession, ClientTimeout, ConnectionTimeoutError
from cactus_test_definitions import TestProcedureId

from cactus_runner.models import (
    ClientInteraction,
    RunnerStatus,
    StartResponseBody,
)

__all__ = ["ClientSession", "ClientTimeout", "RunnerClientException", "TestProcedureId", "RunnerClient"]

logger = logging.getLogger(__name__)


class RunnerClientException(Exception): ...


class RunnerClient:
    @staticmethod
    async def start(session: ClientSession, test_id: TestProcedureId, aggregator_certificate: str) -> StartResponseBody:
        try:
            async with session.post(
                url="/start", params={"test": test_id.value, "certificate": aggregator_certificate}
            ) as response:
                json = await response.text()
                return StartResponseBody.from_json(json)
        except ConnectionTimeoutError as e:
            logger.debug(e)
            raise RunnerClientException("Unexpected failure while starting test.")

    @staticmethod
    async def finalize(session: ClientSession) -> bytes:
        try:
            async with session.post(url="/finalize") as response:
                return await response.read()
        except ConnectionTimeoutError as e:
            logger.debug(e)
            raise RunnerClientException("Unexpected failure while finalizing test procedure.")

    @staticmethod
    async def status(session: ClientSession) -> RunnerStatus:
        try:
            async with session.get(url="/status") as response:
                json = await response.text()
                return RunnerStatus.from_json(json)
        except ConnectionTimeoutError as e:
            logger.debug(e)
            raise RunnerClientException("Unexpected failure while requesting test procedure status.")

    @staticmethod
    async def last_interaction(session: ClientSession) -> ClientInteraction:
        status = await RunnerClient.status(session=session)
        return status.last_client_interaction
