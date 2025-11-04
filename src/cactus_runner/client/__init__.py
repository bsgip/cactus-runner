import logging

from aiohttp import ClientResponse, ClientSession, ClientTimeout, ConnectionTimeoutError
from cactus_test_definitions import CSIPAusVersion
from cactus_test_definitions.client import TestProcedureId

from cactus_runner.models import (
    ClientInteraction,
    InitResponseBody,
    RequestData,
    RequestList,
    RunnerStatus,
    StartResponseBody,
)

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
    async def init(
        session: ClientSession,
        test_id: TestProcedureId,
        csip_aus_version: CSIPAusVersion,
        aggregator_certificate: str | None,
        device_certificate: str | None,
        subscription_domain: str | None = None,
        run_id: str | None = None,
        pen: int = 0,
    ) -> InitResponseBody:
        """
        Args:
            test_id: The TestProcedureId to initialise the runner with
            csip_aus_version: What CSIP Aus version of envoy is this runner communicating with?
            aggregator_certificate: The PEM encoded public certificate to be installed as the "aggregator" cert
            pen: The IANA-registered private enterprise number (PEN). Defaults to 0 (no PEN supplied).
            device_certificate: The PEM encoded public certificate to be reserved for use by a "device"
            subscription_domain: The FQDN that will be added to the allow list for subscription notifications
            run_id: The upstream identifier for this run (to be used in report metadata)"""

        try:
            params = {"test": test_id.value, "csip_aus_version": csip_aus_version.value, "pen": pen}
            if aggregator_certificate is not None:
                params["aggregator_certificate"] = aggregator_certificate
            if device_certificate is not None:
                params["device_certificate"] = device_certificate
            if subscription_domain is not None:
                params["subscription_domain"] = subscription_domain
            if run_id is not None:
                params["run_id"] = run_id

            async with session.post(url="/init", params=params) as response:
                await ensure_success_response(response)
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
            async with session.post(url="/finalize") as response:
                await ensure_success_response(response)
                return await response.read()
        except ConnectionTimeoutError as e:
            logger.debug(e)
            raise RunnerClientException("Unexpected failure while finalizing test procedure.")

    @staticmethod
    async def status(session: ClientSession) -> RunnerStatus:
        try:
            async with session.get(url="/status") as response:
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
            async with session.get(url="/health") as response:
                await ensure_success_response(response)
                return True
        except Exception as e:
            logger.debug(e)
            return False

    @staticmethod
    async def get_request(session: ClientSession, request_id: int) -> RequestData:
        try:
            async with session.get(url=f"/request/{request_id}") as response:
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
            async with session.get(url="/requests") as response:
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
