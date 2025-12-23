import logging

from aiohttp import ClientResponse, ClientSession, ClientTimeout, ConnectionTimeoutError
from cactus_schema.runner import (
    ClientInteraction,
    InitResponseBody,
    RequestData,
    RequestList,
    RunnerStatus,
    RunRequest,
    StartResponseBody,
    uri,
)
from cactus_test_definitions.client import TestProcedureId

__all__ = ["ClientSession", "ClientTimeout", "RunnerClientException", "TestProcedureId", "RunnerClient"]

logger = logging.getLogger(__name__)


class RunnerClientException(Exception):
    http_status_code: int | None  # The HTTP status code received (if any) from the underlying client request
    error_message: str | None  # The error message extracted from the underlying client

    def __init__(self, message: str, http_status_code: int | None = None, error_message: str | None = None) -> None:
        super().__init__(message)
        # Capturing status code in the exception to allow more detailed exception handling by client code.
        # Taking this a step further, we could define app-specific exception codes that can be imported across
        # components.
        self.http_status_code = http_status_code
        self.error_message = error_message


async def ensure_success_response(response: ClientResponse) -> None:
    """Raises a RunnerClientException if the response is NOT a success response (will consume body). Does nothing
    otherwise"""
    if response.status < 200 or response.status > 299:
        try:
            response_body = await response.text()
        except Exception:
            response_body = ""

        logger.error(
            f"Received HTTP {response.status} response for {response.request_info.url}. Response: {response_body}"
        )
        raise RunnerClientException(
            f"Received HTTP {response.status} response from server. Response: {response_body}",
            http_status_code=response.status,
            error_message=response_body,  # We will just pass along the whole body - expecting plaintext
        )


class RunnerClient:
    @staticmethod
    async def initialise(session: ClientSession, run_request: RunRequest) -> InitResponseBody:
        try:
            async with session.post(url=uri.Initialise, data=run_request.to_json()) as response:
                await ensure_success_response(response)
                response_json = await response.text()
                init_response_body = InitResponseBody.from_json(response_json)
                if isinstance(init_response_body, list):
                    raise RunnerClientException(
                        "Unexpected response from server. Expected a single object, but received a list."
                    )
                return init_response_body
        except Exception as e:
            logger.debug(e)
            raise RunnerClientException(f"Unexpected failure while initialising test {e}.")

    @staticmethod
    async def start(session: ClientSession) -> StartResponseBody:
        try:
            async with session.post(url=uri.Start) as response:
                await ensure_success_response(response)
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
            async with session.post(url=uri.Finalize) as response:
                await ensure_success_response(response)
                return await response.read()
        except ConnectionTimeoutError as e:
            logger.debug(e)
            raise RunnerClientException("Unexpected failure while finalizing test procedure.")

    @staticmethod
    async def status(session: ClientSession) -> RunnerStatus:
        try:
            async with session.get(url=uri.Status) as response:
                await ensure_success_response(response)
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

    @staticmethod
    async def health(session: ClientSession) -> bool:
        try:
            async with session.get(url=uri.Health) as response:
                await ensure_success_response(response)
                return True
        except Exception as e:
            logger.debug(e)
            return False

    @staticmethod
    async def get_request(session: ClientSession, request_id: int) -> RequestData:
        try:
            async with session.get(url=uri.Request.format(request_id=request_id)) as response:
                await ensure_success_response(response)
                json = await response.text()
                request_data = RequestData.from_json(json)
                if isinstance(request_data, list):
                    raise RunnerClientException(
                        "Unexpected response from server. Expected a single object, but received a list."
                    )
                return request_data
        except ConnectionTimeoutError as e:
            logger.debug(e)
            raise RunnerClientException(f"Unexpected failure while retrieving request data for ID: {request_id}")

    @staticmethod
    async def list_requests(session: ClientSession) -> RequestList:
        try:
            async with session.get(url=uri.RequestList) as response:
                await ensure_success_response(response)
                json = await response.text()
                request_list = RequestList.from_json(json)
                if isinstance(request_list, list):
                    raise RunnerClientException(
                        "Unexpected response from server. Expected a single object, but received a list."
                    )
                return request_list
        except ConnectionTimeoutError as e:
            logger.debug(e)
            raise RunnerClientException("Unexpected failure while listing request IDs.")
