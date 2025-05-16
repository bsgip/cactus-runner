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
                init_response_body = InitResponseBody.from_json(json)
                if isinstance(init_response_body, list):
                    raise RunnerClientException(
                        "Unexpected response from server. Expected a single object, but received a list."
                    )
                return init_response_body
        except ConnectionTimeoutError as e:
            logger.debug(e)
            raise RunnerClientException("Unexpected failure while initialising test.")

    @staticmethod
    async def start(session: ClientSession) -> StartResponseBody:
        try:
            async with session.post(url="/start") as response:
                json = await response.text()
                start_response_body = StartResponseBody.from_json(json)
                if isinstance(start_response_body, list):
                    raise RunnerClientException(
                        "Unexpected response from server. Expected a single object, but received a list."
                    )
                return start_response_body
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
                runner_status = RunnerStatus.from_json(json)
                if isinstance(runner_status, list):
                    raise RunnerClientException(
                        "Unexpected response from server. Expected a single object, but received a list."
                    )
                return runner_status
        except ConnectionTimeoutError as e:
            logger.debug(e)
            raise RunnerClientException("Unexpected failure while requesting test procedure status.")

    @staticmethod
    async def last_interaction(session: ClientSession) -> ClientInteraction:
        status = await RunnerClient.status(session=session)
        return status.last_client_interaction
