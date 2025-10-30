import io
from pathlib import Path
import zipfile
from urllib.parse import quote

import pytest

from assertical.fixtures.postgres import generate_async_session
from envoy.server.model.site_reading import SiteReading, SiteReadingType

from aiohttp import ClientResponse
from cactus_test_definitions import CSIPAusVersion
from pytest_aiohttp.plugin import TestClient
from sqlalchemy import func, select

from cactus_runner.models import RunnerStatus, StepStatus
from tests.integration.certificate1 import TEST_CERTIFICATE_PEM
from tests.integration.test_all_01 import assert_success_response

URI_ENCODED_CERT = quote(TEST_CERTIFICATE_PEM.decode())


@pytest.mark.parametrize(
    "certificate_type, csip_aus_version",
    [("aggregator_certificate", CSIPAusVersion.BETA_1_3_STORAGE), ("device_certificate", CSIPAusVersion.RELEASE_1_2)],
)
@pytest.mark.slow
@pytest.mark.anyio
async def test_all_01_with_readings(
    cactus_runner_client: TestClient, certificate_type: str, csip_aus_version: CSIPAusVersion, pg_empty_config
):
    """ALL-01 workflow with reading posting and verification"""

    # Load XML data from files
    xml_data_dir = Path(__file__).parent.parent / "data" / "xml"
    edev_xml = (xml_data_dir / "edev.xml").read_text().strip()
    mup_xml = (xml_data_dir / "mup.xml").read_text().strip()
    mmr_xml = (xml_data_dir / "mmr.xml").read_text().strip()

    # SETUP: The real ALL_01 (from test_all_01.py)
    result = await cactus_runner_client.post(
        f"/init?test=ALL-01&{certificate_type}={URI_ENCODED_CERT}&csip_aus_version={csip_aus_version.value}"
    )
    result = await cactus_runner_client.get("/dcap", headers={"ssl-client-cert": URI_ENCODED_CERT})
    result = await cactus_runner_client.get("/edev?s=0&l=100", headers={"ssl-client-cert": URI_ENCODED_CERT})
    result = await cactus_runner_client.get("/tm", headers={"ssl-client-cert": URI_ENCODED_CERT})
    result = await cactus_runner_client.get("/edev/1/der", headers={"ssl-client-cert": URI_ENCODED_CERT})

    # Register the device (required for aggregator certificate)
    if certificate_type == "aggregator_certificate":
        result = await cactus_runner_client.post(
            "/edev", data=edev_xml, headers={"ssl-client-cert": URI_ENCODED_CERT, "Content-Type": "application/sep+xml"}
        )
        await assert_success_response(result)

    # Post Mirror Usage Point
    result = await cactus_runner_client.post(
        "/mup", data=mup_xml, headers={"ssl-client-cert": URI_ENCODED_CERT, "Content-Type": "application/sep+xml"}
    )
    location = result.headers.get("Location")
    mup_id = location.split("/")[-1]
    await assert_success_response(result)

    # Post Readings to Mirror Usage Point
    result: ClientResponse = await cactus_runner_client.post(
        f"/mup/{mup_id}",
        data=mmr_xml,
        headers={"ssl-client-cert": URI_ENCODED_CERT, "Content-Type": "application/sep+xml"},
    )
    await assert_success_response(result)

    # Verify Readings Exist by checking response
    result = await cactus_runner_client.get(f"/mup/{mup_id}", headers={"ssl-client-cert": URI_ENCODED_CERT})
    await assert_success_response(result)
    mup_response = await result.text()
    assert len(mup_response) > 0
    assert "0600006C" in mup_response
    assert "MirrorMeterReading" in mup_response

    # Verify readings were saved to database
    site_id = 2 if certificate_type == "aggregator_certificate" else 1
    expected_reading_count = 3

    async with generate_async_session(pg_empty_config) as session:
        reading_count = (await session.execute(select(func.count()).select_from(SiteReading))).scalar_one()
        assert reading_count == expected_reading_count

        readings = (
            (await session.execute(select(SiteReading).join(SiteReadingType).where(SiteReadingType.site_id == site_id)))
            .scalars()
            .all()
        )
        assert len(readings) == expected_reading_count

    # Finalize - Same as ALL_01 main test but inclusion of readings can change the outcomes
    result = await cactus_runner_client.post("/finalize")
    await assert_success_response(result)
    assert result.headers["Content-Type"] == "application/zip"
    assert "attachment" in result.headers["Content-Disposition"]

    # Verify ZIP file contents
    zip_data = await result.read()
    zip_file = zipfile.ZipFile(io.BytesIO(zip_data))

    def get_filename(prefix: str, filenames: list[str]) -> str:
        """Find first filename that starts with 'prefix'"""
        for filename in filenames:
            if filename.startswith(prefix):
                return filename
        raise Exception(f"No filename prefixed by '{prefix}' found in filenames.")

    # Verify summary
    summary_data = zip_file.read(get_filename(prefix="CactusTestProcedureSummary", filenames=zip_file.namelist()))
    assert len(summary_data) > 0
    summary = RunnerStatus.from_json(summary_data.decode())
    assert summary.csip_aus_version == csip_aus_version.value
    for step, resolved in summary.step_status.items():
        assert resolved.get_step_status() == StepStatus.RESOLVED, step

    # Ensure PDF generated ok
    pdf_data = zip_file.read(get_filename(prefix="CactusTestProcedureReport", filenames=zip_file.namelist()))
    assert len(pdf_data) > 1024, "PDF should be at least 1KB"

    # TO VIEW THE PDF:
    # import os
    # import uuid
    # import tempfile
    # import subprocess

    # with tempfile.NamedTemporaryFile(suffix=".pdf", prefix=f"test_report_{uuid.uuid4().hex[:8]}_", delete=False) as f:
    #     f.write(pdf_data)
    #     f.flush()
    #     print(f"Saved PDF: {f.name}")
    #     if os.environ.get("DISPLAY"):  # Only if running with GUI
    #         subprocess.run(["xdg-open", f.name], check=False)
