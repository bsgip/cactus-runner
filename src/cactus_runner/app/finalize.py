import io
import logging
import os
import shutil
import subprocess  # nosec B404
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

from cactus_runner.app import check, reporting, timeline
from cactus_runner.app.database import (
    DatabaseNotInitialisedError,
    get_postgres_dsn,
)
from cactus_runner.app.envoy_common import (
    get_reading_counts_grouped_by_reading_type,
    get_sites,
)
from cactus_runner.app.log import (
    LOG_FILE_CACTUS_RUNNER,
    LOG_FILE_ENVOY_ADMIN,
    LOG_FILE_ENVOY_NOTIFICATION,
    LOG_FILE_ENVOY_SERVER,
)
from cactus_runner.app.readings import (
    MANDATORY_READING_SPECIFIERS,
    get_readings,
)
from cactus_runner.app.requests_archive import copy_request_response_files_to_archive
from cactus_runner.app.status import get_active_runner_status
from cactus_runner.models import RunnerState

GENERATION_ERRORS_FILE_NAME = "generation-errors.txt"

logger = logging.getLogger(__name__)


class DatabaseDumpError(Exception):
    pass


class NoActiveTestProcedure(Exception):
    pass


def get_file_name_no_extension(file_path: str) -> str:
    """Simple utility for parsing a file path, extracting the file name and removing the last extension (if any).

    eg: turns "/foo.bar/example/file.name.pdf" into "file.name"
    """
    name = Path(file_path).name
    dot_parts = name.split(".")

    if len(dot_parts) == 1:
        return dot_parts[0]  # We don't have a dot in the path name
    else:
        return ".".join(dot_parts[:-1])


def get_zip_contents(
    json_status_summary: str | None,
    log_file_paths: list[str],
    pdf_data: bytes | None,
    errors: list[str],
    filename_infix: str = "",
) -> bytes:
    """Returns the contents of the zipped test procedures artifacts in bytes."""

    writeable_errors = errors.copy()

    # Work in a temporary directory
    with tempfile.TemporaryDirectory() as tempdirname:
        base_path = Path(tempdirname)

        # All the test procedure artifacts should be placed in `archive_dir` to be archived
        archive_dir = base_path / "archive"
        os.mkdir(archive_dir)

        # Create test summary json file
        if json_status_summary is not None:
            file_path = archive_dir / f"CactusTestProcedureSummary{filename_infix}.json"
            with open(file_path, "w") as f:
                f.write(json_status_summary)

        # Copy all log files into the archive - preserving the names
        for log_file_path in log_file_paths:
            log_file_name = get_file_name_no_extension(log_file_path)
            destination = archive_dir / f"{log_file_name}{filename_infix}.log"  # We are assuming .jsonl
            try:
                shutil.copyfile(log_file_path, destination)
            except Exception as exc:
                logger.error(f"Unable to copy {log_file_path} to {destination}", exc_info=exc)
                writeable_errors.append(f"Error fetching cactus logs for {log_file_path}: {exc}")

        # Write pdf report
        if pdf_data is not None:
            file_path = archive_dir / f"CactusTestProcedureReport{filename_infix}.pdf"
            with open(file_path, "wb") as f:
                f.write(pdf_data)

        # Copy request/response files from storage to archive
        copy_request_response_files_to_archive(archive_dir=archive_dir)

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
        except FileNotFoundError as exc:
            logger.error(
                f"Unable to create database snapshot ('{exectuable_name}' executable not found). Did you forget to install 'postgresql-client'?",  # noqa: E501
                exc_info=exc,
            )
            writeable_errors.append(f"Error generating database dump: {exc}")

        # If we have some errors in generating PDF/other outputs - log them in the zip
        if writeable_errors:
            file_path = archive_dir / GENERATION_ERRORS_FILE_NAME
            with open(file_path, "w") as f:
                f.write("\n".join(writeable_errors))

        # Create the temporary zip file
        ARCHIVE_BASEFILENAME = "finalize"
        ARCHIVE_KIND = "zip"
        shutil.make_archive(str(base_path / ARCHIVE_BASEFILENAME), ARCHIVE_KIND, archive_dir)

        # Read the zip file contents as binary
        archive_path = base_path / f"{ARCHIVE_BASEFILENAME}.{ARCHIVE_KIND}"
        with open(archive_path, mode="rb") as f:
            zip_contents = f.read()
    return zip_contents


def safely_get_error_zip(errors: list[str]) -> bytes:
    """Generates a ZIP file containing a single text file with the specified errors being encoded. Use this as a
    last result failover if unable to generate the output data.

    In the event that this fails - no exception will be raised - instead a plaintext error message will encode"""
    try:
        zip_buffer = io.BytesIO()

        # Create a new zip file in write mode
        with zipfile.ZipFile(zip_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zip_file:
            zip_file.writestr("errors.txt", "\n".join(errors))

        return zip_buffer.getvalue()
    except Exception as exc:
        # What else can we do here? It'd still be good to have a "corrupt" ZIP file that can be passed back to us
        # for analysis
        logger.error("Failure to safely generate an error zip.", exc_info=exc)
        return f"Complete failure to generate output zip with data {errors}\nException to follow\n{exc}".encode()


async def generate_pdf(
    runner_state,
    check_results,
    readings,
    reading_counts,
    sites,
    timeline,
    errors,
) -> bytes | None:
    try:
        # Generate the pdf (as bytes)
        pdf_data = reporting.pdf_report_as_bytes(
            runner_state=runner_state,
            check_results=check_results,
            readings=readings,
            reading_counts=reading_counts,
            sites=sites,
            timeline=timeline,
        )
    except Exception as exc:
        logger.error("Error generating PDF report.", exc_info=exc)
        errors.append(f"Error generating PDF report: {exc}")
        pdf_data = None

    # Try generating the report a second time this time without any spacers
    # which seem to be the source of pdf layout issues.
    if pdf_data is None:
        try:
            # Generate the pdf (as bytes) this time excluding any spacers
            pdf_data = reporting.pdf_report_as_bytes(
                runner_state=runner_state,
                check_results=check_results,
                readings=readings,
                reading_counts=reading_counts,
                sites=sites,
                timeline=timeline,
                no_spacers=True,
            )
        except Exception as exc:
            logger.error("Error generating PDF report without Spacers. Omitting report from final zip.", exc_info=exc)
            errors.append(f"Error generating PDF report: {exc}")
            pdf_data = None

    return pdf_data


async def finish_active_test(runner_state: RunnerState, session: AsyncSession) -> bytes:
    """For the specified RunnerState - move the active test into a "Finished" state by calculating the final ZIP
    contents. Raises NoActiveTestProcedure if there isn't an active test procedure for the specified RunnerState

    If the active test is already finished - this will have no effect and will return the cached finished_zip_data

    Populates and then returns the finished_zip_data for the active test procedure"""

    now = datetime.now(timezone.utc)
    errors: list[str] = []  # For capturing basic error information to encode in the zip to alert about missing content

    active_test_procedure = runner_state.active_test_procedure
    if not active_test_procedure:
        raise NoActiveTestProcedure()

    if active_test_procedure.is_finished():
        logger.info(
            f"finish_active_test_procedure: active test procedure {active_test_procedure.name} is already finished"
        )
        return cast(bytes, active_test_procedure.finished_zip_data)  # The is_finished() check guarantees it's not None

    logger.info(f"finish_active_test_procedure: '{active_test_procedure.name}' will be finished")

    # Collect status summary
    try:
        json_status_summary = (
            await get_active_runner_status(
                session=session,
                active_test_procedure=active_test_procedure,
                request_history=runner_state.request_history,
                last_client_interaction=runner_state.last_client_interaction,
            )
        ).to_json()
    except Exception as exc:
        logger.error("Failure generating active runner status", exc_info=exc)
        errors.append(f"Failure generating active runner status: {exc}")
        json_status_summary = None

    check_results = {}
    # Determine all criteria check results
    if active_test_procedure.definition.criteria:
        try:
            check_results = await check.determine_check_results(
                active_test_procedure.definition.criteria.checks,
                active_test_procedure,
                session,
                runner_state.request_history,
            )
        except Exception as exc:
            logger.error("Failed to determine check results", exc_info=exc)
            errors.append(f"Failed to determine check results: {exc}")
            check_results = {}

    # Add a "virtual" check covering XSD errors in incoming requests
    xsd_error_counts = [len(rh.body_xml_errors) for rh in runner_state.request_history if rh.body_xml_errors]
    if xsd_error_counts:
        xsd_check = check.CheckResult(
            False, f"Detected {sum(xsd_error_counts)} xsd errors over {len(xsd_error_counts)} request(s)."
        )
    else:
        xsd_check = check.CheckResult(True, "No XSD errors detected in any requests.")
    check_results["all-requests-xsd-valid"] = xsd_check

    # Figure out the testing timeline
    test_timeline: timeline.Timeline | None = None
    if active_test_procedure.started_at is not None:
        try:
            timeline_interval_seconds = 20
            test_timeline = await timeline.generate_timeline(
                session, start=active_test_procedure.started_at, interval_seconds=timeline_interval_seconds, end=now
            )
            if not test_timeline.data_streams:
                test_timeline = None
        except Exception as exc:
            logger.error("Error generating test timeline data.", exc_info=exc)
            errors.append(f"Failed to generate test timeline: {exc}")
            test_timeline = None

    # Fetch raw DB data and create PDF
    try:
        sites = await get_sites(session)
        readings = await get_readings(session, reading_specifiers=MANDATORY_READING_SPECIFIERS)
        reading_counts = await get_reading_counts_grouped_by_reading_type(session)

        pdf_data = await generate_pdf(
            runner_state=runner_state,
            check_results=check_results,
            readings=readings,
            reading_counts=reading_counts,
            sites=sites,
            timeline=test_timeline,
            errors=errors,
        )
    except Exception as exc:
        logger.error("Failed to generate PDF report", exc_info=exc)
        errors.append(f"Failed to generate PDF report: {exc}")
        pdf_data = None

    generation_timestamp = now.replace(microsecond=0)

    active_test_procedure.finished_zip_data = get_zip_contents(
        json_status_summary=json_status_summary,
        log_file_paths=[
            LOG_FILE_ENVOY_SERVER,
            LOG_FILE_ENVOY_ADMIN,
            LOG_FILE_ENVOY_NOTIFICATION,
            LOG_FILE_CACTUS_RUNNER,
        ],
        pdf_data=pdf_data,
        filename_infix=f"_{int(generation_timestamp.timestamp())}_{active_test_procedure.name}",
        errors=errors,
    )
    return active_test_procedure.finished_zip_data
