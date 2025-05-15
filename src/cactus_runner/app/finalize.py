import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from aiohttp import web

from cactus_runner.app.database import DatabaseNotInitialisedError, get_postgres_dsn

logger = logging.getLogger(__name__)


class DatabaseDumpError(Exception):
    pass


def get_zip_contents(json_status_summary: str, runner_logfile: str, envoy_logfile: str) -> bytes:
    """Returns the contents of the zipped test procedures artifacts in bytes"""
    # Work in a temporary directory
    with tempfile.TemporaryDirectory() as tempdirname:
        base_path = Path(tempdirname)

        # All the test procedure artifacts should be placed in `archive_dir` to be archived
        archive_dir = base_path / "archive"
        os.mkdir(archive_dir)

        # Create test summary json file
        file_path = archive_dir / "test_procedure_summary.json"
        with open(file_path, "w") as f:
            f.write(json_status_summary)

        # Copy Cactus Runner log file into archive
        destination = archive_dir / "cactus_runner.jsonl"
        shutil.copyfile(runner_logfile, destination)

        # Copy Envoy log file into archive
        destination = archive_dir / "envoy.jsonl"
        shutil.copyfile(envoy_logfile, destination)

        # Create db dump
        try:
            connection_string = get_postgres_dsn()
        except DatabaseNotInitialisedError:
            raise DatabaseDumpError("Database is not initialised and therefore cannot be dumped")
        dump_file = str(archive_dir / "envoy_db.dump")
        exectuable_name = "pg_dump"
        command = [
            exectuable_name,
            f"--dbname={connection_string}",
            "-f",
            dump_file,
            "--data-only",
            "--inserts",
            "--no-password",
        ]
        try:
            subprocess.run(command)
        except FileNotFoundError:
            logger.error(
                f"Unable to create database snapshot ('{exectuable_name}' executable not found). Did you forget to install 'postgresql-client'?"  # noqa: E501
            )

        # Create the temporary zip file
        ARCHIVE_BASEFILENAME = "finalize"
        ARCHIVE_KIND = "zip"
        shutil.make_archive(str(base_path / ARCHIVE_BASEFILENAME), ARCHIVE_KIND, archive_dir)

        # Read the zip file contents as binary
        archive_path = base_path / f"{ARCHIVE_BASEFILENAME}.{ARCHIVE_KIND}"
        with open(archive_path, mode="rb") as f:
            zip_contents = f.read()
    return zip_contents


def create_response(json_status_summary: str, runner_logfile: str, envoy_logfile: str) -> web.Response:
    """Creates a finalize test procedure response which includes the test procedure artifacts in zip format"""
    zip_contents = get_zip_contents(
        json_status_summary=json_status_summary, runner_logfile=runner_logfile, envoy_logfile=envoy_logfile
    )

    SUGGESTED_FILENAME = "finalize.zip"
    return web.Response(
        body=zip_contents,
        headers={
            "Content-Type": "application/zip",
            "Content-Disposition": f"attachment; filename={SUGGESTED_FILENAME}",
        },
    )
