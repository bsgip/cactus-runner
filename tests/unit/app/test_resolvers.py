import unittest.mock as mock

import pytest
from assertical.fake.generator import generate_class_instance
from cactus_test_definitions import UnresolvableVariableError

from cactus_runner.models import ActiveTestProcedure, RandomValues
from cactus_runner.plugin.backends.uri import RANDOM_URI_ATTEMPTS, resolve_random_uri


@mock.patch("cactus_runner.plugin.backends.uri.candidate_random_uri")
def test_resolve_random_uri_retries_collisions(mock_candidate_random_uri: mock.MagicMock):
    """In the unlikely event of a URI collision - the attempts are retried"""
    # Arrange
    atp = generate_class_instance(
        ActiveTestProcedure,
        optional_is_none=True,
        random_values=RandomValues(
            random_uri_by_key={
                "abc": "randvalue1",
                "def": "randvalue2",
            }
        ),
    )

    # return colliding values until a distinct value is found
    mock_candidate_random_uri.side_effect = ["randvalue1", "randvalue1", "randvalue2", "randvalue3"]

    # Act
    result = resolve_random_uri(atp, "hij")

    # Assert
    assert result == "randvalue3"
    assert mock_candidate_random_uri.call_count == 4


@mock.patch("cactus_runner.plugin.backends.uri.candidate_random_uri")
def test_resolve_random_uri_bounded_attempts(mock_candidate_random_uri: mock.MagicMock):
    """The system will give up gracefully if collisions cannot be avoided (super unlikely)"""
    # Arrange
    atp = generate_class_instance(
        ActiveTestProcedure,
        optional_is_none=True,
        random_values=RandomValues(random_uri_by_key={"abc": "randvalue1"}),
    )

    # return colliding values until a distinct value is found
    mock_candidate_random_uri.return_value = "randvalue1"

    # Act
    with pytest.raises(UnresolvableVariableError):
        resolve_random_uri(atp, "def")

    # Assert
    assert mock_candidate_random_uri.call_count == RANDOM_URI_ATTEMPTS


def test_resolve_random_uri_stable_value():
    """The value is 'saved' such that successive calls will be the same"""
    # Arrange
    atp = generate_class_instance(
        ActiveTestProcedure,
        optional_is_none=True,
        random_values=RandomValues(random_uri_by_key={}),
    )

    # Act
    uri = resolve_random_uri(atp, "def")
    assert uri == resolve_random_uri(atp, "def")
    assert uri == resolve_random_uri(atp, "def")
    assert uri == resolve_random_uri(atp, "def")

    assert uri.startswith("/")
    assert not uri.endswith("/")
