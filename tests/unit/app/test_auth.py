from unittest.mock import MagicMock

import pytest

from cactus_runner.app.auth import request_is_authorized
from cactus_runner.app.shared import APPKEY_INITIALISED_CERTS


@pytest.mark.parametrize(
    "certificate_fixture,lfdi,expected",
    [
        (
            "aggregator_cert",
            "5b3be900b754e7e6d2dc592170e50ee29ae4e48d",
            True,
        ),
        (
            "device_cert",
            "ae432536c6fc6ddb584903a8b903fcfccb8136fa",
            True,
        ),
        (
            "device_cert",
            "5b3be900b754e7e6d2dc592170e50ee29ae4e48d",
            False,  # Legitimate certificate, incorrect lfdi
        ),
    ],
)
def test_request_is_authorized(
    certificate_fixture: str,
    lfdi: str,
    expected: bool,
    request: pytest.FixtureRequest,
):
    certificate = request.getfixturevalue(certificate_fixture)

    request = MagicMock()
    request.headers = {"ssl-client-cert": certificate}
    request.app[APPKEY_INITIALISED_CERTS].client_lfdi = lfdi

    assert request_is_authorized(request=request) == expected
