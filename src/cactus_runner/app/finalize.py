import logging
import os
import shutil
import subprocess  # nosec B404
import tempfile
from pathlib import Path
from typing import cast

from aiohttp import web
from sqlalchemy.ext.asyncio import AsyncSession

from cactus_runner.app.database import DatabaseNotInitialisedError, get_postgres_dsn
from cactus_runner.app.status import get_active_runner_status
from cactus_runner.models import RunnerState

logger = logging.getLogger(__name__)


class DatabaseDumpError(Exception):
    pass


class NoActiveTestProcedure(Exception):
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
        try:
            shutil.copyfile(runner_logfile, destination)
        except Exception as exc:
            logger.error(f"Unable to copy {runner_logfile} to {destination}", exc_info=exc)

        # Copy Envoy log file into archive
        destination = archive_dir / "envoy.jsonl"
        try:
            shutil.copyfile(envoy_logfile, destination)
        except Exception as exc:
            logger.error(f"Unable to copy {envoy_logfile} to {destination}", exc_info=exc)

        # Create db dump
        try:
            connection_string = get_postgres_dsn().replace("+psycopg", "")
        except DatabaseNotInitialisedError:
            raise DatabaseDumpError("Database is not initialised and therefore cannot be dumped")
        dump_file = str(archive_dir / "envoy_db.dump")
        exectuable_name = "pg_dump"
        # This command isn't constructed from user input, so it should be safe to use subprocess.run (nosec B603)
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
            subprocess.run(command)  # nosec B603
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


async def finish_active_test(runner_state: RunnerState, session: AsyncSession) -> bytes:
    """For the specified RunnerState - move the active test into a "Finished" state by calculating the final ZIP
    contents. Raises NoActiveTestProcedure if there isn't an active test procedure for the specified RunnerState

    If the active test is already finished - this will have no effect and will return the cached finished_zip_data

    Populates and then returns the finished_zip_data for the active test procedure"""

    active_test_procedure = runner_state.active_test_procedure
    if not active_test_procedure:
        raise NoActiveTestProcedure()

    if active_test_procedure.is_finished():
        logger.info(
            f"finish_active_test_procedure: active test procedure {active_test_procedure.name} is already finished"
        )
        return cast(bytes, active_test_procedure.finished_zip_data)  # The is_finished() check guarantees it's not None

    logger.info(f"finish_active_test_procedure: '{active_test_procedure.name}' will be finished")

    json_status_summary = (
        await get_active_runner_status(
            session=session,
            active_test_procedure=active_test_procedure,
            request_history=runner_state.request_history,
            last_client_interaction=runner_state.last_client_interaction,
        )
    ).to_json()

    active_test_procedure.finished_zip_data = get_zip_contents(
        json_status_summary=json_status_summary,
        runner_logfile="logs/cactus_runner.jsonl",
        envoy_logfile="logs/envoy.jsonl",
    )
    return active_test_procedure.finished_zip_data
