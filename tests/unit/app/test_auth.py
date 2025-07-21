from unittest.mock import MagicMock

import pytest

from cactus_runner.app.auth import request_is_authorized
from cactus_runner.app.shared import APPKEY_INITIALISED_CERTS


@pytest.mark.parametrize(
    "certificate_fixture,aggregator_lfdi,device_lfdi,expected",
    [
        (
            "aggregator_cert",
            "5b3be900b754e7e6d2dc592170e50ee29ae4e48d",
            None,
            True,
        ),
        (
            "device_cert",
            "ae432536c6fc6ddb584903a8b903fcfccb8136fa",
            None,
            True,
        ),
        (
            "aggregator_cert",
            None,
            "5b3be900b754e7e6d2dc592170e50ee29ae4e48d",
            True,
        ),
        (
            "device_cert",
            None,
            "ae432536c6fc6ddb584903a8b903fcfccb8136fa",
            True,
        ),
        (
            "device_cert",
            "ae432536c6fc6ddb584903a8b903fcfccb8136fa",
            "5b3be900b754e7e6d2dc592170e50ee29ae4e48d",
            True,
        ),
        (
            "device_cert",
            "5b3be900b754e7e6d2dc592170e50ee29ae4e48d",
            "ae432536c6fc6ddb584903a8b903fcfccb8136fa",
            True,
        ),
        (
            "device_cert",
            "5b3be900b754e7e6d2dc592170e50ee29ae4e48d",
            None,
            False,  # Legitimate certificate, incorrect lfdi
        ),
        (
            "device_cert",
            None,
            "5b3be900b754e7e6d2dc592170e50ee29ae4e48d",
            False,  # Legitimate certificate, incorrect lfdi
        ),
        (
            "aggregator_cert",
            "97ebe8886754aaaad2dc592170aaaee12213eaa9",
            "5b3be900b754e7e6d2dc592170e50ee29ae4e48d",
            False,  # Legitimate certificate, incorrect lfdi
        ),
        (
            "aggregator_cert",
            None,
            None,
            False,
        ),
    ],
)
def test_request_is_authorized(
    certificate_fixture: str,
    aggregator_lfdi: str | None,
    device_lfdi: str | None,
    expected: bool,
    request: pytest.FixtureRequest,
):
    certificate = request.getfixturevalue(certificate_fixture)

    request = MagicMock()
    request.headers = {"ssl-client-cert": certificate}
    request.app[APPKEY_INITIALISED_CERTS].aggregator_lfdi = aggregator_lfdi
    request.app[APPKEY_INITIALISED_CERTS].device_lfdi = device_lfdi

    assert request_is_authorized(request=request) == expected
