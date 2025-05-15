import logging

from aiohttp import ClientSession, ClientTimeout, ConnectionTimeoutError
from cactus_test_definitions import TestProcedureId

from cactus_runner.models import (
    ClientInteraction,
    InitResponseBody,
    RunnerStatus,
    StartResponseBody,
)

__all__ = ["ClientSession", "ClientTimeout", "RunnerClientException", "TestProcedureId", "RunnerClient"]

logger = logging.getLogger(__name__)


class RunnerClientException(Exception): ...  # noqa: E701


class RunnerClient:
    @staticmethod
    async def init(session: ClientSession, test_id: TestProcedureId, aggregator_certificate: str) -> InitResponseBody:
        try:
            async with session.post(
                url="/init", params={"test": test_id.value, "certificate": aggregator_certificate}
            ) as response:
                json = await response.text()
                return InitResponseBody.from_json(json)
        except ConnectionTimeoutError as e:
            logger.debug(e)
            raise RunnerClientException("Unexpected failure while initialising test.")

    @staticmethod
    async def start(session: ClientSession) -> StartResponseBody:
        try:
            async with session.post(url="/start") as response:
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
