import unittest.mock as mock
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from assertical.asserts.type import assert_list_type
from assertical.fake.generator import generate_class_instance
from assertical.fake.sqlalchemy import assert_mock_session, create_mock_session
from assertical.fixtures.postgres import generate_async_session
from envoy.server.model.archive.doe import (
    ArchiveDynamicOperatingEnvelope,
    ArchiveSiteControlGroupDefault,
)
from envoy.server.model.doe import DynamicOperatingEnvelope, SiteControlGroupDefault
from envoy.server.model.site_reading import SiteReading, SiteReadingType
from intervaltree import Interval, IntervalTree

from cactus_runner.app.envoy_common import ReadingLocation
from cactus_runner.app.timeline import (
    Timeline,
    TimelineDataStream,
    decimal_to_watts,
    duration_to_label,
    generate_control_data_streams,
    generate_default_control_data_streams,
    generate_offset_watt_values,
    generate_readings_data_stream,
    generate_timeline,
    highest_priority_entity,
    pow10_to_watts,
    reading_to_watts,
)

BASIS = datetime(2022, 1, 2, 3, 4, 5, 6, tzinfo=timezone.utc)  # Used as an arbitrary - non aligned datetime


@pytest.mark.parametrize(
    "value, expected",
    [(0, "start"), (43, "43s"), (-20, "-20s"), (123, "2m3s"), (-123, "-2m3s"), (-180, "-3m"), (240, "4m")],
)
def test_duration_to_label(value, expected):
    result = duration_to_label(value)
    assert isinstance(result, str)
    assert result == expected


@pytest.mark.parametrize("value, expected", [(None, None), (Decimal("123"), 123), (Decimal("2.74"), 2)])
def test_decimal_to_watts(value, expected):
    result = decimal_to_watts(value, False)
    assert type(result) is type(expected)
    assert result == expected

    result_negated = decimal_to_watts(value, True)
    assert type(result_negated) is type(expected)
    if expected is not None:
        assert result_negated == -1 * expected
    else:
        assert result_negated is None


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
        ),  # Active entities always take precedence over deleted archive records
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
        (
            [
                generate_class_instance(
                    ArchiveDynamicOperatingEnvelope, seed=101, changed_time=datetime(2021, 1, 1, tzinfo=timezone.utc)
                ),
                generate_class_instance(
                    DynamicOperatingEnvelope, seed=202, changed_time=datetime(2022, 1, 1, tzinfo=timezone.utc)
                ),
                generate_class_instance(
                    ArchiveDynamicOperatingEnvelope,
                    seed=303,
                    deleted_time=None,
                    changed_time=datetime(2023, 1, 1, tzinfo=timezone.utc),
                ),
            ],
            2,
        ),  # Archive records take precedence
        (
            [
                generate_class_instance(
                    ArchiveDynamicOperatingEnvelope,
                    seed=101,
                    deleted_time=None,
                    archive_time=datetime(2021, 1, 2, tzinfo=timezone.utc),  # highest archive time for tiebreak
                ),
                generate_class_instance(
                    DynamicOperatingEnvelope, seed=202, changed_time=datetime(2022, 1, 1, tzinfo=timezone.utc)
                ),
                generate_class_instance(
                    ArchiveDynamicOperatingEnvelope,
                    seed=303,
                    deleted_time=None,
                    archive_time=datetime(2021, 1, 1, tzinfo=timezone.utc),
                ),
                generate_class_instance(
                    ArchiveDynamicOperatingEnvelope,
                    seed=404,
                    archive_time=datetime(2021, 1, 1, tzinfo=timezone.utc),
                ),
            ],
            0,
        ),  # Archive time is tiebreaker on archive records
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
        [
            lambda x: decimal_to_watts(x.import_limit_active_watts, False),
            lambda x: decimal_to_watts(x.export_limit_watts, False),
        ],
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
    interval_seconds = 5
    mock_session = create_mock_session()
    srt1 = generate_class_instance(SiteReadingType, seed=101, power_of_ten_multiplier=-1, site_reading_type_id=1)
    srt2 = generate_class_instance(SiteReadingType, seed=202, power_of_ten_multiplier=1, site_reading_type_id=2)
    mock_get_csip_aus_site_reading_types.return_value = [srt1, srt2]
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
        ),  # This will be highest priority due to having the highest changed_time
    ]
    mock_get_site_readings.side_effect = lambda _, srt: srt1_readings if srt is srt1 else srt2_readings

    # Act
    result = await generate_readings_data_stream(
        mock_session, "bar", ReadingLocation.DEVICE_READING, BASIS, BASIS + timedelta(seconds=10), interval_seconds
    )

    # Assert
    assert isinstance(result, TimelineDataStream)
    assert result.label == "bar"
    assert isinstance(result.offset_watt_values, list)
    assert len(result.offset_watt_values) == 2, "10 seconds of 5 second intervals"
    assert result.offset_watt_values == [3330, 22], "Values adjusted for pow10 in SiteReadingType"

    assert_mock_session(mock_session)
    mock_get_csip_aus_site_reading_types.assert_called_once()
    mock_get_site_readings.assert_has_calls(
        [mock.call(mock_session, srt1), mock.call(mock_session, srt2)], any_order=True
    )


@mock.patch("cactus_runner.app.timeline.get_csip_aus_site_reading_types")
@mock.patch("cactus_runner.app.timeline.get_site_readings")
@pytest.mark.asyncio
async def test_generate_readings_data_stream_filters_null_and_zero_durations(
    mock_get_site_readings: mock.MagicMock, mock_get_csip_aus_site_reading_types: mock.MagicMock
):
    # Arrange
    interval_seconds = 5
    mock_session = create_mock_session()
    srt = generate_class_instance(SiteReadingType, seed=101, power_of_ten_multiplier=0, site_reading_type_id=1)
    mock_get_csip_aus_site_reading_types.return_value = [srt]

    readings = [
        # Valid
        generate_class_instance(
            SiteReading, seed=101, site_reading_type_id=1, value=100, time_period_start=BASIS, time_period_seconds=5
        ),
        # Zero duration - should be filtered out
        generate_class_instance(
            SiteReading,
            seed=202,
            site_reading_type_id=1,
            value=200,
            time_period_start=BASIS + timedelta(seconds=5),
            time_period_seconds=0,
        ),
        # Null duration - should be filtered out
        generate_class_instance(
            SiteReading,
            seed=303,
            site_reading_type_id=1,
            value=300,
            time_period_start=BASIS + timedelta(seconds=10),
            time_period_seconds=None,
        ),
        # Another valid reading
        generate_class_instance(
            SiteReading,
            seed=404,
            site_reading_type_id=1,
            value=400,
            time_period_start=BASIS + timedelta(seconds=10),
            time_period_seconds=5,
        ),
    ]
    mock_get_site_readings.return_value = readings

    # Act
    result = await generate_readings_data_stream(
        mock_session, "test", ReadingLocation.SITE_READING, BASIS, BASIS + timedelta(seconds=15), interval_seconds
    )

    # Assert
    assert isinstance(result, TimelineDataStream)
    assert result.label == "test"
    assert len(result.offset_watt_values) == 3, "15 seconds of 5 second intervals"
    # Only the two valid readings (100W and 400W) should be included, middle is filtered out leaving none
    assert result.offset_watt_values == [100, None, 400], "Only valid duration readings included"

    assert_mock_session(mock_session)
    mock_get_csip_aus_site_reading_types.assert_called_once()
    mock_get_site_readings.assert_called_once_with(mock_session, srt)


def doe(
    seed: int,
    start: datetime,
    duration: int,
    deleted_time: datetime | None = None,
    archive_time: datetime | None = None,
    scg: int = 1,  # Site Control Group
    imp_watts: int | None = None,
    exp_watts: int | None = None,
    load_watts: int | None = None,
    gen_watts: int | None = None,
    superseded: bool = False,
) -> DynamicOperatingEnvelope | ArchiveDynamicOperatingEnvelope:
    """Utility function for reducing boilerplate"""

    extra_kwargs = {}
    t = DynamicOperatingEnvelope

    if archive_time is not None:
        t = ArchiveDynamicOperatingEnvelope
        extra_kwargs = {"archive_time": archive_time}

    if deleted_time is not None:
        t = ArchiveDynamicOperatingEnvelope
        if archive_time is None:
            extra_kwargs = {"deleted_time": deleted_time, "archive_time": deleted_time}

    return generate_class_instance(
        t,
        seed=seed,
        site_control_group_id=scg,
        import_limit_active_watts=Decimal(imp_watts) if imp_watts is not None else None,
        export_limit_watts=Decimal(exp_watts) if exp_watts is not None else None,
        load_limit_active_watts=Decimal(load_watts) if load_watts is not None else None,
        generation_limit_active_watts=Decimal(gen_watts) if gen_watts is not None else None,
        start_time=start,
        end_time=start + timedelta(seconds=duration),
        duration_seconds=duration,
        superseded=superseded,
        **extra_kwargs,
    )


@pytest.mark.parametrize(
    "controls, start, interval, end, expected",
    [
        ([], BASIS, 5, BASIS + timedelta(seconds=10), []),
        (
            [doe(101, BASIS, 5, imp_watts=1, exp_watts=2, load_watts=3, gen_watts=4)],
            BASIS,
            5,
            BASIS + timedelta(seconds=10),
            [[1, None], [-2, None], [3, None], [-4, None]],
        ),
        (
            [
                doe(
                    101,
                    BASIS,
                    10,
                    deleted_time=BASIS + timedelta(seconds=5),
                    imp_watts=1,
                    exp_watts=2,
                    load_watts=3,
                    gen_watts=4,
                )
            ],
            BASIS,
            5,
            BASIS + timedelta(seconds=10),
            [[1, None], [-2, None], [3, None], [-4, None]],
        ),  # Control was active for only the first 5 seconds before being deleted
        (
            [
                doe(101, BASIS, 5, imp_watts=11, exp_watts=None, load_watts=33, gen_watts=None),
                doe(202, BASIS + timedelta(seconds=5), 5, imp_watts=None, exp_watts=22, load_watts=None, gen_watts=44),
                doe(303, BASIS + timedelta(seconds=10), 5, imp_watts=99, exp_watts=99, load_watts=99, gen_watts=99),
            ],
            BASIS,
            5,
            BASIS + timedelta(seconds=10),
            [[11, None], [None, -22], [33, None], [None, -44]],
        ),  # Multiple Controls, some out of range of the interval period - no overlaps - with None values in controls
        (
            [
                doe(101, BASIS, 5, imp_watts=11, exp_watts=12, load_watts=13, gen_watts=14),
                doe(202, BASIS, 5, imp_watts=21, exp_watts=22, load_watts=23, gen_watts=24),
                doe(
                    303,
                    BASIS,
                    120,
                    deleted_time=BASIS + timedelta(seconds=60),
                    imp_watts=31,
                    exp_watts=32,
                    load_watts=33,
                    gen_watts=34,
                ),
                doe(404, BASIS + timedelta(seconds=10), 5, imp_watts=41, exp_watts=42, load_watts=43, gen_watts=44),
            ],
            BASIS,
            5,
            BASIS + timedelta(seconds=20),
            [[21, 31, 41, 31], [-22, -32, -42, -32], [23, 33, 43, 33], [-24, -34, -44, -34]],
        ),  # Multiple Controls with overlaps
        (
            [
                doe(101, BASIS, 60, scg=1, imp_watts=11, exp_watts=12, load_watts=13, gen_watts=14),
                doe(202, BASIS, 5, scg=1, imp_watts=21, exp_watts=22, load_watts=23, gen_watts=24),
                doe(
                    303, BASIS + timedelta(seconds=5), 5, scg=2, imp_watts=31, exp_watts=32, load_watts=33, gen_watts=34
                ),
                doe(404, BASIS, 60, scg=3, imp_watts=41, exp_watts=42, load_watts=43, gen_watts=44),
            ],
            BASIS,
            5,
            BASIS + timedelta(seconds=10),
            [
                [21, 11],
                [-22, -12],
                [23, 13],
                [-24, -14],  # SCG 1
                [None, 31],
                [None, -32],
                [None, 33],
                [None, -34],  # SCG 2
                [41, 41],
                [-42, -42],
                [43, 43],
                [-44, -44],  # SCG 3
            ],
        ),  # Multiple control groups - mix of overlapping
        (
            [
                doe(
                    101,
                    BASIS,
                    60,
                    imp_watts=11,
                    exp_watts=21,
                    load_watts=31,
                    gen_watts=41,
                    superseded=True,
                ),  # This is the "updated" long DERControl
                doe(
                    101,
                    BASIS,
                    60,
                    archive_time=BASIS + timedelta(seconds=10),
                    imp_watts=21,
                    exp_watts=22,
                    load_watts=23,
                    gen_watts=24,
                    superseded=False,
                ),  # This is the "archive" long DERControl - containing the original values
                doe(
                    202,
                    BASIS + timedelta(seconds=10),
                    20,
                    deleted_time=BASIS + timedelta(seconds=20),
                    imp_watts=31,
                    exp_watts=32,
                    load_watts=33,
                    gen_watts=34,
                    superseded=False,
                ),  # This is the short DERControl that appeared, ran for a bit and then was cancelled
            ],
            BASIS,
            10,
            BASIS + timedelta(seconds=40),
            [[21, 31, None, None], [-22, -32, None, None], [23, 33, None, None], [-24, -34, None, None]],
        ),  # Long control that gets superseded by a short control that is cancelled partway through.
    ],
)
@mock.patch("cactus_runner.app.timeline.get_site_controls_active_archived")
@pytest.mark.asyncio
async def test_generate_control_data_streams(
    mock_get_site_controls_active_deleted: mock.MagicMock, controls, start, interval, end, expected
):
    """Checks that generate_control_data_streams breaks down DOE data into seperate "DERProgram" streams.

    expected has the form:
    [
        # These 4 lists will be repeated for EACH distinct SiteControlGroup
        [opModImpLimW vals]
        [opModExpLimW vals]
        [opModLoadLimW vals]
        [opModGenLimW vals]
    ]
    """
    # Arrange
    mock_session = create_mock_session()

    mock_get_site_controls_active_deleted.return_value = controls

    # Act
    result = await generate_control_data_streams(mock_session, start, end, interval)

    # Assert
    assert_list_type(TimelineDataStream, result, len(expected))
    assert len(set((ds.label for ds in result))) == len(result), "Expecting unique labels"
    actual = [ds.offset_watt_values for ds in result]
    assert expected == actual

    assert_mock_session(mock_session)
    mock_get_site_controls_active_deleted.assert_called_once_with(mock_session)


def def_ctrl(
    seed: int,
    changed_time: datetime,
    archive_time: datetime | None = None,
    imp_watts: int | None = None,
    exp_watts: int | None = None,
    load_watts: int | None = None,
    gen_watts: int | None = None,
) -> SiteControlGroupDefault | ArchiveSiteControlGroupDefault:
    """Utility function for reducing boilerplate"""
    if archive_time is None:
        t = SiteControlGroupDefault
        extra_kwargs = {}
    else:
        t = ArchiveSiteControlGroupDefault
        extra_kwargs = {"archive_time": archive_time, "deleted_time": None}

    return generate_class_instance(
        t,
        seed=seed,
        import_limit_active_watts=Decimal(imp_watts) if imp_watts is not None else None,
        export_limit_active_watts=Decimal(exp_watts) if exp_watts is not None else None,
        load_limit_active_watts=Decimal(load_watts) if load_watts is not None else None,
        generation_limit_active_watts=Decimal(gen_watts) if gen_watts is not None else None,
        changed_time=changed_time,
        **extra_kwargs,
    )


@pytest.mark.parametrize(
    "defaults, start, interval, end, expected",
    [
        ([], BASIS, 5, BASIS + timedelta(seconds=10), []),
        (
            [def_ctrl(101, BASIS, imp_watts=1, exp_watts=2, load_watts=3, gen_watts=4)],
            BASIS - timedelta(seconds=5),
            5,
            BASIS + timedelta(seconds=10),
            [[None, 1, 1], [None, -2, -2], [None, 3, 3], [None, -4, -4]],
        ),
        (
            [
                def_ctrl(101, BASIS + timedelta(seconds=10), imp_watts=11, exp_watts=12, load_watts=13, gen_watts=14),
                def_ctrl(
                    202,
                    BASIS + timedelta(seconds=5),
                    archive_time=BASIS + timedelta(seconds=10),
                    imp_watts=21,
                    exp_watts=None,
                    load_watts=23,
                    gen_watts=None,
                ),
                def_ctrl(
                    303,
                    BASIS,
                    archive_time=BASIS + timedelta(seconds=5),
                    imp_watts=None,
                    exp_watts=32,
                    load_watts=None,
                    gen_watts=34,
                ),
            ],
            BASIS,
            5,
            BASIS + timedelta(seconds=15),
            [[None, 21, 11], [-32, None, -12], [None, 23, 13], [-34, None, -14]],
        ),
    ],
)
@mock.patch("cactus_runner.app.timeline.get_site_control_group_defaults_with_archive")
@pytest.mark.asyncio
async def test_generate_default_control_data_streams(
    mock_get_site_control_group_defaults_with_archive: mock.MagicMock, defaults, start, interval, end, expected
):

    mock_session = create_mock_session()

    mock_get_site_control_group_defaults_with_archive.return_value = defaults

    # Act
    result = await generate_default_control_data_streams(mock_session, start, end, interval)

    # Assert
    assert_list_type(TimelineDataStream, result, len(expected))
    assert len(set((ds.label for ds in result))) == len(expected), "Expecting unique labels"
    actual = [ds.offset_watt_values for ds in result]
    assert expected == actual

    assert_mock_session(mock_session)
    mock_get_site_control_group_defaults_with_archive.assert_called_once_with(mock_session)


@pytest.mark.parametrize(
    "site_vals, device_vals, control_vals, default_vals, expected",
    [
        ([], [], [], [], []),
        (
            [None, None],
            [None, None],
            [[None, None], [None, None], [None, None], [None, None]],
            [[None, None], [None, None]],
            [],
        ),
        (
            [None, 1],
            [None, None],
            [[None, None], [2, None], [None, 3], [None, None]],
            [[None, None], [4, 4]],
            [[None, 1], [2, None], [None, 3], [4, 4]],
        ),
        (
            [1, 2],
            [3, 4],
            [[None, None], [2, None], [None, 3], [None, None]],
            [[None, None], [4, 4]],
            [[1, 2], [3, 4], [2, None], [None, 3], [4, 4]],
        ),
    ],
)
@mock.patch("cactus_runner.app.timeline.generate_readings_data_stream")
@mock.patch("cactus_runner.app.timeline.generate_control_data_streams")
@mock.patch("cactus_runner.app.timeline.generate_default_control_data_streams")
@pytest.mark.asyncio
async def test_generate_timeline(
    mock_generate_default_control_data_streams: mock.MagicMock,
    mock_generate_control_data_streams: mock.MagicMock,
    mock_generate_readings_data_stream: mock.MagicMock,
    site_vals: list[int | None],
    device_vals: list[int | None],
    control_vals: list[list[int | None]],
    default_vals: list[list[int | None]],
    expected: list[list[int | None]],
):
    """Checks the top level behaviour of generate_timeline - Focusing on the culling of "superfluous" streams"""
    # Arrange
    start = BASIS
    end = BASIS + timedelta(seconds=5)
    interval = 5
    mock_session = create_mock_session()

    site_ds = generate_class_instance(TimelineDataStream, seed=101, offset_watt_values=site_vals)
    device_ds = generate_class_instance(TimelineDataStream, seed=202, offset_watt_values=device_vals)
    control_ds = [
        generate_class_instance(TimelineDataStream, seed=300 + idx, offset_watt_values=vals)
        for idx, vals in enumerate(control_vals)
    ]
    default_ds = [
        generate_class_instance(TimelineDataStream, seed=400 + idx, offset_watt_values=vals)
        for idx, vals in enumerate(default_vals)
    ]

    mock_generate_readings_data_stream.side_effect = lambda sesion, label, location, start, end, interval_seconds: (
        site_ds if location == ReadingLocation.SITE_READING else device_ds
    )
    mock_generate_control_data_streams.return_value = control_ds
    mock_generate_default_control_data_streams.return_value = default_ds

    # Act
    result = await generate_timeline(mock_session, start, interval, end)

    # Assert
    assert_mock_session(mock_session)
    assert isinstance(result, Timeline)
    assert_list_type(TimelineDataStream, result.data_streams, len(expected))
    assert result.interval_seconds == interval
    assert result.start == start

    actual = [ds.offset_watt_values for ds in result.data_streams]
    assert expected == actual
