import logging
import os
import shutil
import subprocess  # nosec B404
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

from cactus_runner.app import check, reporting
from cactus_runner.app.database import (
    DatabaseNotInitialisedError,
    begin_session,
    get_postgres_dsn,
)
from cactus_runner.app.envoy_common import get_sites
from cactus_runner.app.log import LOG_FILE_CACTUS_RUNNER, LOG_FILE_ENVOY
from cactus_runner.app.readings import (
    MANDATORY_READING_SPECIFIERS,
    get_reading_counts,
    get_readings,
)
from cactus_runner.app.status import get_active_runner_status
from cactus_runner.models import RunnerState

logger = logging.getLogger(__name__)


class DatabaseDumpError(Exception):
    pass


class NoActiveTestProcedure(Exception):
    pass


def get_zip_contents(
    json_status_summary: str, runner_logfile: str, envoy_logfile: str, pdf_data: bytes, filename_infix: str = ""
) -> bytes:
    """Returns the contents of the zipped test procedures artifacts in bytes"""
    # Work in a temporary directory
    with tempfile.TemporaryDirectory() as tempdirname:
        base_path = Path(tempdirname)

        # All the test procedure artifacts should be placed in `archive_dir` to be archived
        archive_dir = base_path / "archive"
        os.mkdir(archive_dir)

        # Create test summary json file
        file_path = archive_dir / f"CactusTestProcedureSummary{filename_infix}.json"
        with open(file_path, "w") as f:
            f.write(json_status_summary)

        # Copy Cactus Runner log file into archive
        destination = archive_dir / f"CactusRunnerLog{filename_infix}.jsonl"
        try:
            shutil.copyfile(runner_logfile, destination)
        except Exception as exc:
            logger.error(f"Unable to copy {runner_logfile} to {destination}", exc_info=exc)

        # Copy Envoy log file into archive
        destination = archive_dir / f"EnvoyLog{filename_infix}.jsonl"
        try:
            shutil.copyfile(envoy_logfile, destination)
        except Exception as exc:
            logger.error(f"Unable to copy {envoy_logfile} to {destination}", exc_info=exc)

        # Write pdf report
        file_path = archive_dir / f"CactusTestProcedureReport{filename_infix}.pdf"
        with open(file_path, "wb") as f:
            f.write(pdf_data)

        # Create db dump
        try:
            connection_string = get_postgres_dsn().replace("+psycopg", "")
        except DatabaseNotInitialisedError:
            raise DatabaseDumpError("Database is not initialised and therefore cannot be dumped")
        dump_file = str(archive_dir / f"EnvoyDB{filename_infix}.dump")
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

    # Determine all criteria check results
    check_results = {}
    if active_test_procedure.definition.criteria:
        async with begin_session() as session:
            check_results = await check.determine_check_results(
                active_test_procedure.definition.criteria.checks, active_test_procedure, session
            )

    # Determine readings for the CSIP-AUS mandatory reading types
    readings = await get_readings(reading_specifiers=MANDATORY_READING_SPECIFIERS)

    # Determine reading counts
    reading_counts = await get_reading_counts()

    # Determine sites
    sites = await get_sites(session=session)
    if not sites:
        sites = []

    # Generate the pdf (as bytes)
    pdf_data = reporting.pdf_report_as_bytes(
        runner_state=runner_state,
        check_results=check_results,
        readings=readings,
        reading_counts=reading_counts,
        sites=list(sites),
    )

    generation_timestamp = datetime.now(timezone.utc).replace(microsecond=0)

    active_test_procedure.finished_zip_data = get_zip_contents(
        json_status_summary=json_status_summary,
        runner_logfile=LOG_FILE_CACTUS_RUNNER,
        envoy_logfile=LOG_FILE_ENVOY,
        pdf_data=pdf_data,
        filename_infix=f"_{generation_timestamp.isoformat()}_{active_test_procedure.name}",
    )
    return active_test_procedure.finished_zip_data
