import io
import json
import shutil
import zipfile
from dataclasses import asdict
from urllib.parse import quote

import pytest
from aiohttp import ClientSession, ClientTimeout
from cactus_schema.runner import RunnerStatus, RunRequest, uri
from cactus_test_definitions import CSIPAusVersion
from cactus_test_definitions.client import TestProcedureId
from pytest_aiohttp.plugin import TestClient

from cactus_runner.app.finalize import PLAYLIST_ZIP_DIR
from cactus_runner.client import RunnerClient, ensure_success_response
from tests.integration.certificate1 import TEST_CERTIFICATE_PEM

URI_ENCODED_CERT = quote(TEST_CERTIFICATE_PEM.decode())


async def run_all_01_requests(client: TestClient) -> None:
    """Make the standard requests needed to complete an ALL-01 test."""
    result = await client.get("/dcap", headers={"ssl-client-cert": URI_ENCODED_CERT})
    await ensure_success_response(result)
    result = await client.get("/edev?s=0&l=100", headers={"ssl-client-cert": URI_ENCODED_CERT})
    await ensure_success_response(result)
    result = await client.get("/tm", headers={"ssl-client-cert": URI_ENCODED_CERT})
    await ensure_success_response(result)
    result = await client.get("/edev/1/der", headers={"ssl-client-cert": URI_ENCODED_CERT})
    await ensure_success_response(result)


def verify_zip_contents(zip_data: bytes, expected_test_name: str) -> None:
    """Verify that a ZIP file contains expected test artifacts."""
    zip_file = zipfile.ZipFile(io.BytesIO(zip_data))
    filenames = zip_file.namelist()

    # Should have summary JSON
    summary_files = [f for f in filenames if f.startswith("CactusTestProcedureSummary")]
    assert len(summary_files) >= 1, f"Missing summary file in {filenames}"

    # Should have PDF report
    pdf_files = [f for f in filenames if f.startswith("CactusTestProcedureReport")]
    assert len(pdf_files) >= 1, f"Missing PDF report in {filenames}"

    # Verify summary contains expected test name
    summary_data = zip_file.read(summary_files[0])
    summary = RunnerStatus.from_json(summary_data.decode())
    assert (
        summary.test_procedure_name == expected_test_name
    ), f"Expected test name '{expected_test_name}', got '{summary.test_procedure_name}'"


@pytest.mark.slow
@pytest.mark.anyio
async def test_playlist_two_tests(cactus_runner_client: TestClient, run_request_generator):
    """Test running a playlist of two tests sequentially.

    This verifies:
    1. Playlist initialization with multiple RunRequests
    2. First test runs and finalizes correctly
    3. Second test auto-initializes after first finalize
    4. Second test runs and finalizes correctly
    5. ZIPs are saved to filesystem for each test
    """
    # Clear any existing playlist ZIPs
    if PLAYLIST_ZIP_DIR.exists():
        shutil.rmtree(PLAYLIST_ZIP_DIR)

    # Create two test requests - using ALL-01 (immediate start) for simplicity
    agg_cert = TEST_CERTIFICATE_PEM.decode()
    csip_version = CSIPAusVersion.RELEASE_1_2

    run_request_1: RunRequest = run_request_generator(TestProcedureId.ALL_01, agg_cert, None, csip_version, None)
    run_request_2: RunRequest = run_request_generator(TestProcedureId.ALL_01, agg_cert, None, csip_version, None)

    # Modify run_ids to distinguish them
    run_request_1 = RunRequest(
        run_id="playlist-test-1",
        test_definition=run_request_1.test_definition,
        run_group=run_request_1.run_group,
        test_config=run_request_1.test_config,
        test_user=run_request_1.test_user,
    )
    run_request_2 = RunRequest(
        run_id="playlist-test-2",
        test_definition=run_request_2.test_definition,
        run_group=run_request_2.run_group,
        test_config=run_request_2.test_config,
        test_user=run_request_2.test_user,
    )

    playlist = [run_request_1, run_request_2]

    # Initialize playlist
    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(60)) as session:
        await RunnerClient.initialise(session, playlist)

        # Verify first test is initialized
        status = await RunnerClient.status(session)
        assert status.test_procedure_name == TestProcedureId.ALL_01.value

    # Run first test
    await run_all_01_requests(cactus_runner_client)

    # Finalize first test - should return ZIP and auto-init second test
    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(60)) as session:
        zip_data_1 = await RunnerClient.finalize(session)
        assert len(zip_data_1) > 0
        verify_zip_contents(zip_data_1, TestProcedureId.ALL_01.value)

        # Verify ZIP was saved to filesystem
        assert PLAYLIST_ZIP_DIR.exists(), "Playlist ZIP directory should exist"
        saved_zips = list(PLAYLIST_ZIP_DIR.glob("*.zip"))
        assert len(saved_zips) >= 1, f"Expected at least 1 saved ZIP, found {len(saved_zips)}"

        # Verify second test is now initialized (playlist advanced)
        status = await RunnerClient.status(session)
        assert status.test_procedure_name == TestProcedureId.ALL_01.value

    # Run second test
    await run_all_01_requests(cactus_runner_client)

    # Finalize second test
    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(60)) as session:
        zip_data_2 = await RunnerClient.finalize(session)
        assert len(zip_data_2) > 0
        verify_zip_contents(zip_data_2, TestProcedureId.ALL_01.value)

        # Verify both ZIPs are saved (first one + second one saved on finalize)
        saved_zips = list(PLAYLIST_ZIP_DIR.glob("*.zip"))
        assert len(saved_zips) >= 2, f"Expected at least 2 saved ZIPs, found {len(saved_zips)}"

        # Verify no active test after playlist completes
        status = await RunnerClient.status(session)
        assert status.test_procedure_name == "-", "No test should be active after playlist completes"


@pytest.mark.slow
@pytest.mark.anyio
async def test_playlist_single_test_backwards_compatible(cactus_runner_client: TestClient, run_request_generator):
    """Test that a single RunRequest (non-playlist) still works as before.

    This ensures backwards compatibility - existing single-test workflows are unaffected.
    """
    agg_cert = TEST_CERTIFICATE_PEM.decode()
    csip_version = CSIPAusVersion.RELEASE_1_2

    run_request: RunRequest = run_request_generator(TestProcedureId.ALL_01, agg_cert, None, csip_version, None)

    # Initialize single test (not a playlist)
    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(60)) as session:
        init_response = await RunnerClient.initialise(session, run_request)
        assert init_response.is_started

    # Run test
    result = await cactus_runner_client.get("/dcap", headers={"ssl-client-cert": URI_ENCODED_CERT})
    await ensure_success_response(result)

    result = await cactus_runner_client.get("/edev?s=0&l=100", headers={"ssl-client-cert": URI_ENCODED_CERT})
    await ensure_success_response(result)

    # Finalize
    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(60)) as session:
        zip_data = await RunnerClient.finalize(session)
        verify_zip_contents(zip_data, TestProcedureId.ALL_01.value)

        # Verify no active test after finalize
        status = await RunnerClient.status(session)
        assert status.test_procedure_name == "-"


@pytest.mark.slow
@pytest.mark.anyio
async def test_playlist_preserves_site_data(cactus_runner_client: TestClient, run_request_generator):
    """Test that site/aggregator data is preserved between playlist tests.

    This verifies the partial database reset works correctly - site registration
    from test 1 should still be valid in test 2.
    """
    # Clear any existing playlist ZIPs
    if PLAYLIST_ZIP_DIR.exists():
        shutil.rmtree(PLAYLIST_ZIP_DIR)

    agg_cert = TEST_CERTIFICATE_PEM.decode()
    csip_version = CSIPAusVersion.RELEASE_1_2

    run_request_1: RunRequest = run_request_generator(TestProcedureId.ALL_01, agg_cert, None, csip_version, None)
    run_request_2: RunRequest = run_request_generator(TestProcedureId.ALL_01, agg_cert, None, csip_version, None)

    playlist = [run_request_1, run_request_2]

    # Initialize playlist
    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(60)) as session:
        await RunnerClient.initialise(session, playlist)

    # First test - register a site via requests
    result = await cactus_runner_client.get("/dcap", headers={"ssl-client-cert": URI_ENCODED_CERT})
    await ensure_success_response(result)

    # This registers the end device
    result = await cactus_runner_client.get("/edev?s=0&l=100", headers={"ssl-client-cert": URI_ENCODED_CERT})
    await ensure_success_response(result)

    # Get DER info (requires registered device)
    result = await cactus_runner_client.get("/edev/1/der", headers={"ssl-client-cert": URI_ENCODED_CERT})
    await ensure_success_response(result)

    # Finalize first test
    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(60)) as session:
        await RunnerClient.finalize(session)

    # Second test - site should still be registered (preserved by partial reset)
    # The aggregator/certificate registration persists, allowing the same certificate to work
    result = await cactus_runner_client.get("/dcap", headers={"ssl-client-cert": URI_ENCODED_CERT})
    await ensure_success_response(result)

    # This should still work because the aggregator is preserved
    result = await cactus_runner_client.get("/edev?s=0&l=100", headers={"ssl-client-cert": URI_ENCODED_CERT})
    await ensure_success_response(result)

    # Finalize second test
    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(60)) as session:
        zip_data_2 = await RunnerClient.finalize(session)
        assert len(zip_data_2) > 0


@pytest.mark.anyio
async def test_playlist_empty_rejected(cactus_runner_client: TestClient):
    """Test that an empty playlist is rejected with 400 Bad Request."""
    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(30)) as session:
        async with session.post(url=uri.Initialise, data="[]") as response:
            assert response.status == 400
            body = await response.text()
            assert "Empty playlist" in body


@pytest.mark.slow
@pytest.mark.anyio
async def test_playlist_with_start_index(cactus_runner_client: TestClient, run_request_generator):
    """
    Test that start_index skips to the correct test in a playlist.

    This verifies:
    1. Tests before start_index are skipped
    2. Test at start_index becomes the active test
    3. Remaining tests after start_index execute normally
    4. ZIP filenames reflect the original playlist position
    """
    # Clear any existing playlist ZIPs
    if PLAYLIST_ZIP_DIR.exists():
        shutil.rmtree(PLAYLIST_ZIP_DIR)

    agg_cert = TEST_CERTIFICATE_PEM.decode()
    csip_version = CSIPAusVersion.RELEASE_1_2

    # Create 4 test requests
    run_requests = []
    for i in range(4):
        rr = run_request_generator(TestProcedureId.ALL_01, agg_cert, None, csip_version, None)
        rr = RunRequest(
            run_id=f"playlist-test-{i}",
            test_definition=rr.test_definition,
            run_group=rr.run_group,
            test_config=rr.test_config,
            test_user=rr.test_user,
        )
        run_requests.append(rr)

    # Initialize playlist with start_index=2 (skip first 2 tests)
    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(60)) as session:
        await RunnerClient.initialise(session, run_requests, start_index=2)

        # Verify test at index 2 is active
        status = await RunnerClient.status(session)
        assert status.test_procedure_name == TestProcedureId.ALL_01.value

    # Run test at index 2
    await run_all_01_requests(cactus_runner_client)

    # Finalize test at index 2
    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(60)) as session:
        zip_data_2 = await RunnerClient.finalize(session)
        assert len(zip_data_2) > 0

        # Verify test at index 3 is now active
        status = await RunnerClient.status(session)
        assert status.test_procedure_name == TestProcedureId.ALL_01.value

    # Run test at index 3
    await run_all_01_requests(cactus_runner_client)

    # Finalize test at index 3
    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(60)) as session:
        zip_data_3 = await RunnerClient.finalize(session)
        assert len(zip_data_3) > 0

        # no active test after playlist completes
        status = await RunnerClient.status(session)
        assert status.test_procedure_name == "-"

    # Verify ZIP files were saved with correct indices (2 and 3, not 0 and 1)
    saved_zips = sorted(PLAYLIST_ZIP_DIR.glob("*.zip"))
    assert len(saved_zips) >= 2

    # ZIP filenames should include the playlist index (002_ and 003_)
    zip_names = [z.name for z in saved_zips]
    assert any("002_" in name for name in zip_names)
    assert any("003_" in name for name in zip_names)


@pytest.mark.anyio
async def test_playlist_invalid_start_index_rejected(cactus_runner_client: TestClient, run_request_generator):
    agg_cert = TEST_CERTIFICATE_PEM.decode()
    csip_version = CSIPAusVersion.RELEASE_1_2

    # Create 3 test requests
    run_requests = []
    for i in range(3):
        rr = run_request_generator(TestProcedureId.ALL_01, agg_cert, None, csip_version, None)
        run_requests.append(rr)

    # Try to initialize with start_index=5 (out of bounds)
    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(30)) as session:
        json_data = [asdict(rr) for rr in run_requests]
        async with session.post(
            url=f"{uri.Initialise}?start_index=5",
            data=json.dumps(json_data),
            headers={"Content-Type": "application/json"},
        ) as response:
            assert response.status == 400
            body = await response.text()
            assert "start_index" in body


@pytest.mark.slow
@pytest.mark.anyio
async def test_playlist_three_tests(cactus_runner_client: TestClient, run_request_generator):
    """Test running a playlist of three tests to verify the loop works correctly."""
    # Clear any existing playlist ZIPs
    if PLAYLIST_ZIP_DIR.exists():
        shutil.rmtree(PLAYLIST_ZIP_DIR)

    agg_cert = TEST_CERTIFICATE_PEM.decode()
    csip_version = CSIPAusVersion.RELEASE_1_2

    # Create three test requests
    run_requests = []
    for i in range(3):
        rr = run_request_generator(TestProcedureId.ALL_01, agg_cert, None, csip_version, None)
        rr = RunRequest(
            run_id=f"playlist-test-{i+1}",
            test_definition=rr.test_definition,
            run_group=rr.run_group,
            test_config=rr.test_config,
            test_user=rr.test_user,
        )
        run_requests.append(rr)

    # Initialize playlist
    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(60)) as session:
        await RunnerClient.initialise(session, run_requests)

    # Run and finalize each test
    for i in range(3):
        # Verify a test is active
        async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(60)) as session:
            status = await RunnerClient.status(session)
            assert status.test_procedure_name == TestProcedureId.ALL_01.value, f"Test {i+1}: Expected active test"

        # Make minimum requests to complete test
        await run_all_01_requests(cactus_runner_client)

        # Finalize
        async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(60)) as session:
            zip_data = await RunnerClient.finalize(session)
            assert len(zip_data) > 0

    # Verify all ZIPs saved
    saved_zips = list(PLAYLIST_ZIP_DIR.glob("*.zip"))
    assert len(saved_zips) >= 3, f"Expected at least 3 saved ZIPs, found {len(saved_zips)}"

    # Verify no active test
    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(60)) as session:
        status = await RunnerClient.status(session)
        assert status.test_procedure_name == "-"
