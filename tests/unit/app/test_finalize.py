import io
import random
import string
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pandas as pd
import pytest
from assertical.fake.generator import generate_class_instance

from cactus_runner.app import finalize
from cactus_runner.models import (
    CheckResult,
    ReadingType,
    ReportingData,
    RunnerState,
    Site,
)

DT_NOW = datetime.now(timezone.utc)


@pytest.mark.parametrize(
    "input, expected",
    [
        ("", ""),
        ("/", ""),
        ("/foo/bar/", "bar"),
        ("/foo/bar", "bar"),
        ("/foo/bar/example.pdf", "example"),
        ("/foo.bar/baz/example.pdf", "example"),
        ("/foo.bar/baz/example.with.dots.pdf", "example.with.dots"),
    ],
)
def test_get_file_name_no_extension(input, expected):
    actual = finalize.get_file_name_no_extension(input)
    assert isinstance(actual, str)
    assert actual == expected


def test_get_zip_contents(mocker):
    """
    NOTE: This test uses a mock to disable the database dump and so doesn't
        verify the 'envoy_db.dump' is written into the zip archive.
    """

    def random_string(length: int) -> str:
        return "".join(random.choices(string.ascii_uppercase + string.digits, k=length))

    expected_postgres_dsn = "fake:dsn//value"
    get_postgres_dsn_mock = mocker.patch("cactus_runner.app.finalize.get_postgres_dsn")
    get_postgres_dsn_mock.return_value = expected_postgres_dsn
    subprocess_run_mock = mocker.patch.object(finalize.subprocess, "run")  # prevent db dump

    json_status_summary = random_string(length=100)
    json_reporting_data = random_string(length=100)
    contents_of_logfile1 = bytes(random_string(length=100), encoding="utf-8")
    contents_of_logfile2 = bytes(random_string(length=100), encoding="utf-8")
    pdf_data = bytes(random_string(length=100), encoding="utf-8")  # not legimate pdf data
    errors = []

    with (
        tempfile.NamedTemporaryFile(delete_on_close=False) as logfile1,
        tempfile.NamedTemporaryFile(delete_on_close=False) as logfile2,
    ):
        logfile1_name = logfile1.name
        logfile1.write(contents_of_logfile1)
        logfile1.close()

        logfile2_name = logfile2.name
        logfile2.write(contents_of_logfile2)
        logfile2.close()

        zip_contents = finalize.get_zip_contents(
            json_status_summary=json_status_summary,
            json_reporting_data=json_reporting_data,
            log_file_paths=[logfile1_name, logfile2_name],
            pdf_data=pdf_data,
            errors=errors,
        )

    zip = zipfile.ZipFile(io.BytesIO(zip_contents))
    filenames = zip.namelist()

    def get_filename(prefix: str, filenames: list[str]) -> str:
        """Find first filename that starts with 'prefix'"""
        for filename in filenames:
            if filename.startswith(prefix):
                return filename
        return ""

    assert isinstance(zip_contents, bytes)
    assert zip.read(get_filename(prefix="CactusTestProcedureSummary", filenames=filenames)) == bytes(
        json_status_summary, encoding="utf-8"
    )

    assert (
        zip.read(get_filename(prefix=finalize.get_file_name_no_extension(logfile1_name), filenames=filenames))
        == contents_of_logfile1
    )
    assert (
        zip.read(get_filename(prefix=finalize.get_file_name_no_extension(logfile2_name), filenames=filenames))
        == contents_of_logfile2
    )
    subprocess_run_mock.assert_called_once()
    assert len(errors) == 0, "This shouldn't have been mutated"


def test_safely_get_error_zip():
    errors = ["my first error", "my second error"]

    # Act
    zip_contents = finalize.safely_get_error_zip(errors)

    # Assert
    assert isinstance(zip_contents, bytes)
    assert len(zip_contents) > 0
    zip = zipfile.ZipFile(io.BytesIO(zip_contents))
    filenames = zip.namelist()
    assert len(filenames) == 1, "There should only be a single filename"

    unzipped_errors = zip.read(filenames[0]).decode()
    for e in errors:
        assert e in unzipped_errors


def test_safely_get_error_with_error(mocker):
    """If we hit an error generating the zip file - return a plaintext stream of bytes as a failover"""
    errors = ["my first error", "my second error"]

    zipfile_mock = mocker.patch("cactus_runner.app.finalize.zipfile.ZipFile")
    exception_msg = "mock exception 123 abc"
    zipfile_mock.side_effect = Exception(exception_msg)

    # Act
    zip_contents = finalize.safely_get_error_zip(errors)

    # Assert
    assert isinstance(zip_contents, bytes)
    assert len(zip_contents) > 0
    assert exception_msg in zip_contents.decode(), "Its ugly - but what else can we do?"


def test_get_zip_contents_with_errors(mocker):
    """
    NOTE: This test uses a mock to disable the database dump and so doesn't
        verify the 'envoy_db.dump' is written into the zip archive.
    """

    expected_postgres_dsn = "fake:dsn//value"
    get_postgres_dsn_mock = mocker.patch("cactus_runner.app.finalize.get_postgres_dsn")
    get_postgres_dsn_mock.return_value = expected_postgres_dsn

    errors = ["my long error string", "my other error"]

    zip_contents = finalize.get_zip_contents(
        json_status_summary=None,
        json_reporting_data=None,
        log_file_paths=["file-that-dne.txt", "file-that-dne-2.txt"],
        pdf_data=None,
        errors=errors,
    )

    zip = zipfile.ZipFile(io.BytesIO(zip_contents))
    filenames = zip.namelist()

    def get_filename(prefix: str, filenames: list[str]) -> str:
        """Find first filename that starts with 'prefix'"""
        for filename in filenames:
            if filename.startswith(prefix):
                return filename
        return ""

    assert isinstance(zip_contents, bytes)
    zipped_errors = zip.read(get_filename(prefix=finalize.GENERATION_ERRORS_FILE_NAME, filenames=filenames)).decode()
    assert errors[0] in zipped_errors
    assert errors[1] in zipped_errors
    assert len(errors) == 2, "This shouldn't have been mutated"


@pytest.mark.asyncio
async def test_generate_json_reporting_data():
    # Arrange
    def check_results(num=3, passed=True, description=None):
        return {
            f"check{i}": generate_class_instance(CheckResult, passed=passed, description=description)
            for i in range(num)
        }

    def fake_readings(reading_types: list[ReadingType]):
        NUMBER_OF_READINGS = 5
        sample_df = pd.DataFrame(
            {
                "scaled_value": [Decimal(random.random()) for _ in range(NUMBER_OF_READINGS)],
                "time_period_start": [DT_NOW + timedelta(seconds=_ * 5) for _ in range(NUMBER_OF_READINGS)],
            }
        )
        return {rt: sample_df for rt in reading_types}

    def fake_reading_counts(reading_types: list[ReadingType]):
        return {rt: random.randrange(10, 500) for rt in reading_types}

    runner_state = RunnerState()
    checks = check_results()
    site_reading_type_counts = 3
    site_count = 5
    reading_types = [generate_class_instance(ReadingType) for _ in range(site_reading_type_counts)]
    readings = fake_readings(reading_types)
    reading_counts = fake_reading_counts(reading_types)
    sites = [generate_class_instance(Site) for _ in range(site_count)]
    timeline = None
    errors = []

    # Act
    reporting_data_str = await finalize.generate_json_reporting_data(
        runner_state=runner_state,
        check_results=checks,
        readings=readings,
        reading_counts=reading_counts,
        sites=sites,
        timeline=timeline,
        errors=errors,
    )
    print(reporting_data_str)
    reporting_data = ReportingData.from_json(reporting_data_str)

    # Assert
    print(reporting_data)
