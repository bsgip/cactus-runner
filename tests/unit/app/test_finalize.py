import io
import random
import string
import tempfile
import zipfile

import pytest
from aiohttp.web import Response

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
        )

    zip = zipfile.ZipFile(io.BytesIO(zip_contents))

    assert isinstance(zip_contents, bytes)
    assert zip.read("test_procedure_summary.json") == bytes(json_status_summary, encoding="utf-8")
    assert zip.read("cactus_runner.jsonl") == contents_of_logfile
    subprocess_run_mock.assert_called_once()


def test_get_zip_contents_raises_databasedumperror(mocker):

    mocker.patch.object(finalize.shutil, "copyfile")  # prevent logfile copying
    mocker.patch("cactus_runner.app.database.CURRENT_CONNECTION", None)  # Need to mock this out incase previous
    # test initialises DB.

    with pytest.raises(finalize.DatabaseDumpError):
        finalize.get_zip_contents(json_status_summary="", runner_logfile="", envoy_logfile="")


def test_create_response(mocker):
    mocked_zip_contents = random.randbytes(50)
    get_zip_contents_mock = mocker.patch("cactus_runner.app.finalize.get_zip_contents")
    get_zip_contents_mock.return_value = mocked_zip_contents

    response = finalize.create_response(json_status_summary="", runner_logfile="", envoy_logfile="")

    assert isinstance(response, Response)
    assert response.status == 200
    assert response.content_type == "application/zip"
    assert response.body == mocked_zip_contents
