import unittest.mock as mock
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from assertical.fake.generator import generate_class_instance
from assertical.fake.sqlalchemy import assert_mock_session, create_mock_session
from assertical.fixtures.postgres import generate_async_session
from envoy.server.model.archive.doe import ArchiveDynamicOperatingEnvelope
from envoy.server.model.archive.site import ArchiveDefaultSiteControl
from envoy.server.model.doe import DynamicOperatingEnvelope
from envoy.server.model.site import DefaultSiteControl
from envoy.server.model.site_reading import SiteReading, SiteReadingType
from envoy_schema.server.schema.sep2.types import (
    DataQualifierType,
    KindType,
    RoleFlagsType,
    UomType,
)
from intervaltree import Interval, IntervalTree

from cactus_runner.app.envoy_common import ReadingLocation
from cactus_runner.app.timeline import (
    TimelineDataStream,
    decimal_to_watts,
    generate_offset_watt_values,
    generate_readings_data_stream,
    highest_priority_entity,
    pow10_to_watts,
    reading_to_watts,
)


@pytest.mark.parametrize("value, expected", [(None, None), (Decimal("-123"), -123), (Decimal("2.74"), 2)])
def test_decimal_to_watts(value, expected):
    result = decimal_to_watts(value)
    assert type(result) is type(expected)
    assert result == expected


@pytest.mark.parametrize("value, pow10, expected", [(123, 0, 123), (123, -1, 12), (129, -1, 12), (123, 2, 12300)])
def test_pow10_to_watts(value, pow10, expected):
    result = pow10_to_watts(value, pow10)
    assert type(result) is type(expected)
    assert result == expected


@pytest.mark.parametrize(
    "srts, reading, expected",
    [
        (
            [
                generate_class_instance(SiteReadingType, seed=101, site_reading_type_id=11, power_of_ten_multiplier=-1),
                generate_class_instance(SiteReadingType, seed=202, site_reading_type_id=22, power_of_ten_multiplier=2),
            ],
            generate_class_instance(SiteReading, seed=303, site_reading_type_id=11, value=123),
            12,
        ),
        (
            [
                generate_class_instance(SiteReadingType, seed=101, site_reading_type_id=11, power_of_ten_multiplier=-1),
                generate_class_instance(SiteReadingType, seed=202, site_reading_type_id=22, power_of_ten_multiplier=2),
            ],
            generate_class_instance(SiteReading, seed=303, site_reading_type_id=22, value=123),
            12300,
        ),
        (
            [
                generate_class_instance(SiteReadingType, seed=101, site_reading_type_id=11, power_of_ten_multiplier=-1),
                generate_class_instance(SiteReadingType, seed=202, site_reading_type_id=22, power_of_ten_multiplier=2),
            ],
            generate_class_instance(SiteReading, seed=303, site_reading_type_id=2, value=123),
            ValueError,
        ),
    ],
)
def test_reading_to_watts(srts, reading, expected):
    if isinstance(expected, type):
        with pytest.raises(expected):
            reading_to_watts(srts, reading)
    else:
        result = reading_to_watts(srts, reading)
        assert type(result) is type(expected)
        assert result == expected


@pytest.mark.parametrize(
    "entities, expected_index",
    [
        ([], ValueError),
        ([generate_class_instance(SiteReading)], 0),
        (
            [
                generate_class_instance(SiteReading, seed=101, changed_time=datetime(2022, 1, 1, tzinfo=timezone.utc)),
                generate_class_instance(SiteReading, seed=202, changed_time=datetime(2021, 1, 1, tzinfo=timezone.utc)),
            ],
            0,
        ),  # changed_time is tiebreaker
        (
            [
                generate_class_instance(
                    DynamicOperatingEnvelope, seed=101, changed_time=datetime(2021, 1, 1, tzinfo=timezone.utc)
                ),
                generate_class_instance(
                    ArchiveDynamicOperatingEnvelope, seed=202, changed_time=datetime(2022, 1, 1, tzinfo=timezone.utc)
                ),
                generate_class_instance(
                    ArchiveDynamicOperatingEnvelope, seed=303, changed_time=datetime(2023, 1, 1, tzinfo=timezone.utc)
                ),
            ],
            0,
        ),  # Active entities always take precedence
        (
            [
                generate_class_instance(
                    ArchiveDynamicOperatingEnvelope, seed=101, changed_time=datetime(2021, 1, 1, tzinfo=timezone.utc)
                ),
                generate_class_instance(
                    ArchiveDynamicOperatingEnvelope, seed=202, changed_time=datetime(2022, 1, 1, tzinfo=timezone.utc)
                ),
                generate_class_instance(
                    ArchiveDynamicOperatingEnvelope, seed=303, changed_time=datetime(2023, 1, 1, tzinfo=timezone.utc)
                ),
            ],
            2,
        ),  # changed_time is tiebreaker
    ],
)
def test_highest_priority_entity(entities, expected_index):
    intervals = [Interval(idx, idx + 1, e) for idx, e in enumerate(entities)]

    if isinstance(expected_index, type):
        with pytest.raises(expected_index):
            highest_priority_entity(intervals)
    else:
        # Test intervals in forward and reverse
        result = highest_priority_entity(set(intervals))
        assert result is entities[expected_index]
        result = highest_priority_entity(reversed(intervals))
        assert result is entities[expected_index]


BASIS = datetime(2022, 1, 2, 3, 4, 5, 6, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "interval_length_seconds, start, end, expected_result",
    [
        (20, BASIS, BASIS + timedelta(seconds=50), [[2, 4, 1], [22, 44, 11]]),
        (60, BASIS, BASIS + timedelta(seconds=50), [[4], [44]]),
        (20, BASIS - timedelta(seconds=1), BASIS + timedelta(seconds=50), [[2, 4, 4], [22, 44, 44]]),
        (25, BASIS, BASIS + timedelta(seconds=75), [[4, 4, 1], [44, 44, 11]]),
    ],
)
def test_generate_offset_watt_values(interval_length_seconds, start, end, expected_result):
    """This test has a fixed set of intervals - all the parameters vary how those intervals are queried"""
    intervals = [
        Interval(
            BASIS - timedelta(days=9999),
            BASIS + timedelta(days=9999),
            generate_class_instance(
                ArchiveDynamicOperatingEnvelope,
                seed=101,
                changed_time=datetime(2021, 1, 1, tzinfo=timezone.utc),
                import_limit_active_watts=Decimal("1"),
                export_limit_watts=Decimal("11"),
            ),
        ),
        Interval(
            BASIS,
            BASIS + timedelta(seconds=20),
            generate_class_instance(
                DynamicOperatingEnvelope,
                seed=202,
                changed_time=datetime(2021, 1, 1, tzinfo=timezone.utc),
                import_limit_active_watts=Decimal("2"),
                export_limit_watts=Decimal("22"),
            ),
        ),
        Interval(
            BASIS,
            BASIS + timedelta(seconds=40),
            generate_class_instance(
                DynamicOperatingEnvelope,
                seed=303,
                changed_time=datetime(2020, 1, 1, tzinfo=timezone.utc),
                import_limit_active_watts=Decimal("3"),
                export_limit_watts=Decimal("33"),
            ),
        ),
        Interval(
            BASIS + timedelta(seconds=20),
            BASIS + timedelta(seconds=40),
            generate_class_instance(
                DynamicOperatingEnvelope,
                seed=404,
                changed_time=datetime(2021, 1, 1, 9, tzinfo=timezone.utc),
                import_limit_active_watts=Decimal("4"),
                export_limit_watts=Decimal("44"),
            ),
        ),
        Interval(
            BASIS + timedelta(seconds=20),
            BASIS + timedelta(seconds=40),
            generate_class_instance(
                ArchiveDynamicOperatingEnvelope,
                seed=505,
                changed_time=datetime(2025, 1, 1, tzinfo=timezone.utc),
                import_limit_active_watts=Decimal("5"),
                export_limit_watts=Decimal("55"),
            ),
        ),
        Interval(
            BASIS + timedelta(seconds=20),
            BASIS + timedelta(seconds=60),
            generate_class_instance(
                ArchiveDynamicOperatingEnvelope,
                seed=606,
                changed_time=datetime(2020, 1, 1, tzinfo=timezone.utc),
                import_limit_active_watts=Decimal("6"),
                export_limit_watts=Decimal("66"),
            ),
        ),
    ]
    tree = IntervalTree(intervals)

    result = generate_offset_watt_values(
        tree,
        start,
        end,
        interval_length_seconds,
        [lambda x: decimal_to_watts(x.import_limit_active_watts), lambda x: decimal_to_watts(x.export_limit_watts)],
    )
    assert isinstance(result, list)
    assert len(result) == 2, "Two lambdas were used - should have two resulting lists"
    assert result == expected_result


@pytest.mark.asyncio
async def test_generate_readings_data_stream_empty_db(pg_empty_config):
    async with generate_async_session(pg_empty_config) as session:
        result = await generate_readings_data_stream(
            session, "foo", ReadingLocation.SITE_READING, BASIS, BASIS + timedelta(seconds=10), 1
        )

    assert isinstance(result, TimelineDataStream)
    assert result.label == "foo"
    assert isinstance(result.offset_watt_values, list)
    assert len(result.offset_watt_values) == 10, "10 seconds of 1 second intervals"
    assert all((v is None for v in result.offset_watt_values))


@mock.patch("cactus_runner.app.timeline.get_csip_aus_site_reading_types")
@mock.patch("cactus_runner.app.timeline.get_site_readings")
@pytest.mark.asyncio
async def test_generate_readings_data_stream(
    mock_get_site_readings: mock.MagicMock, mock_get_csip_aus_site_reading_types: mock.MagicMock
):
    # Arrange
    mock_session = create_mock_session()
    srt1 = generate_class_instance(SiteReadingType, seed=101, power_of_ten_multiplier=-1, site_reading_type_id=1)
    srt2 = generate_class_instance(SiteReadingType, seed=202, power_of_ten_multiplier=1, site_reading_type_id=2)
    mock_get_site_readings.return_value = [srt1, srt2]
    srt1_readings = [
        generate_class_instance(
            SiteReading,
            seed=101,
            site_reading_type_id=1,
            value=111,
            time_period_start=BASIS - timedelta(seconds=2),
            time_period_seconds=5,
        ),
        generate_class_instance(
            SiteReading,
            seed=202,
            site_reading_type_id=1,
            value=222,
            time_period_start=BASIS + timedelta(seconds=5),
            time_period_seconds=5,
        ),
    ]
    srt2_readings = [
        generate_class_instance(
            SiteReading,
            seed=303,
            site_reading_type_id=2,
            value=333,
            time_period_start=BASIS,
            time_period_seconds=5,
        ),
    ]
    mock_get_site_readings.side_effect = lambda _, srt: srt1_readings if srt is srt1 else srt2_readings

    # Act
    result = await generate_readings_data_stream(
        mock_session, "bar", ReadingLocation.DEVICE_READING, BASIS, BASIS + timedelta(seconds=10), 5
    )

    # Assert
    assert isinstance(result, TimelineDataStream)
    assert result.label == "bar"
    assert isinstance(result.offset_watt_values, list)
    assert len(result.offset_watt_values) == 2, "10 seconds of 5 second intervals"
    assert result.offset_watt_values == [22, 3330]

    assert_mock_session(mock_session)
    mock_get_site_readings.assert_called_once()
    mock_get_site_readings.assert_has_calls(
        [mock.call(mock_session, srt1), mock.call(mock_session, srt2)], any_order=True
    )
