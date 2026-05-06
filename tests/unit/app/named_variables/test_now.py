from datetime import UTC, datetime

import freezegun
import pytest
from assertical.asserts.time import assert_nowish

from cactus_runner.app import resolvers


def test_resolve_named_variable_now():
    actual = resolvers.resolve_named_variable_now()
    assert actual.tzinfo
    assert_nowish(actual)


@pytest.mark.parametrize(
    "now, expected",
    [
        (
            datetime(2021, 11, 1, 4, 0, 0, 0, tzinfo=UTC),
            datetime(2021, 11, 1, 14, 0, 0, 0, tzinfo=resolvers.AEST),
        ),
        (
            datetime(2022, 11, 14, 0, 0, 0, 0, tzinfo=resolvers.AEST),
            datetime(2022, 11, 14, 0, 0, 0, 0, tzinfo=resolvers.AEST),
        ),
        (
            datetime(2021, 10, 19, 20, 2, 3, 4, tzinfo=UTC),
            datetime(2021, 10, 20, 6, 0, 0, 0, tzinfo=resolvers.AEST),
        ),
        (
            datetime(2022, 9, 1, 1, 2, 3, 4, tzinfo=resolvers.AEST),
            datetime(2022, 9, 1, 1, 0, 0, 0, tzinfo=resolvers.AEST),
        ),
    ],
)
def test_resolve_named_variable_now_hour(now: datetime, expected: datetime):

    with freezegun.freeze_time(now):
        actual = resolvers.resolve_named_variable_now_hour()
        assert actual.tzinfo == resolvers.AEST
        assert actual == expected, f"Diff={(actual - expected).total_seconds()} seconds"

    assert actual.minute == 0
    assert actual.second == 0
    assert actual.microsecond == 0


@pytest.mark.parametrize(
    "now, expected",
    [
        (
            datetime(2021, 11, 1, 4, 0, 0, 0, tzinfo=UTC),
            datetime(2021, 11, 1, 0, 0, 0, 0, tzinfo=resolvers.AEST),
        ),
        (
            datetime(2021, 11, 1, 20, 0, 0, 0, tzinfo=UTC),
            datetime(2021, 11, 2, 0, 0, 0, 0, tzinfo=resolvers.AEST),
        ),
        (
            datetime(2021, 11, 1, 20, 1, 2, 3, tzinfo=UTC),
            datetime(2021, 11, 2, 0, 0, 0, 0, tzinfo=resolvers.AEST),
        ),
    ],
)
def test_resolve_named_variable_now_day(now: datetime, expected: datetime):
    with freezegun.freeze_time(now):
        actual = resolvers.resolve_named_variable_now_day()
        assert actual.tzinfo == resolvers.AEST
        assert actual == expected, f"Diff={(actual - expected).total_seconds()} seconds"

    assert actual.hour == 0
    assert actual.minute == 0
    assert actual.second == 0
    assert actual.microsecond == 0
