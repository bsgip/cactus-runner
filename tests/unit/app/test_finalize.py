import io
import random
import string
import tempfile
import zipfile

from cactus_runner.app import finalize


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
    contents_of_logfile = bytes(random_string(length=100), encoding="utf-8")
    pdf_data = bytes(random_string(length=100), encoding="utf-8")  # not legimate pdf data
    errors = []

    with (
        tempfile.NamedTemporaryFile(delete_on_close=False) as runner_logfile,
        tempfile.NamedTemporaryFile(delete_on_close=False) as envoy_logfile,
    ):
        runner_logfile.write(contents_of_logfile)
        runner_logfile.close()

        envoy_logfile.write(contents_of_logfile)
        envoy_logfile.close()

        zip_contents = finalize.get_zip_contents(
            json_status_summary=json_status_summary,
            runner_logfile=runner_logfile.name,
            envoy_logfile=envoy_logfile.name,
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
    assert zip.read(get_filename(prefix="CactusRunnerLog", filenames=filenames)) == contents_of_logfile
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
        runner_logfile="file-that-dne.txt",
        envoy_logfile="file-that-dne-2.txt",
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
    zipped_errors = zip.read(get_filename(prefix="generation-errors", filenames=filenames)).decode()
    assert errors[0] in zipped_errors
    assert errors[1] in zipped_errors
    assert len(errors) == 2, "This shouldn't have been mutated"
