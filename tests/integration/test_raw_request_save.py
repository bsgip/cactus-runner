from pathlib import Path
from cactus_test_definitions import CSIPAusVersion
from fastapi.testclient import TestClient
import pytest
from tests.integration.test_all_01 import URI_ENCODED_CERT, assert_success_response


@pytest.mark.slow
@pytest.mark.anyio
async def test_request_data_retrieval_endpoints(cactus_runner_client: TestClient, pg_empty_config):
    """Test retrieval of raw request/response data via /requests and /request/{request_id} endpoints"""

    # SETUP: Run through ALL-01 workflow to generate request/response data
    result = await cactus_runner_client.post(
        f"/init?test=ALL-01&device_certificate={URI_ENCODED_CERT}&csip_aus_version={CSIPAusVersion.RELEASE_1_2.value}"
    )
    await assert_success_response(result)

    result = await cactus_runner_client.get("/dcap", headers={"ssl-client-cert": URI_ENCODED_CERT})
    await assert_success_response(result)

    result = await cactus_runner_client.get("/edev?s=0&l=100", headers={"ssl-client-cert": URI_ENCODED_CERT})
    await assert_success_response(result)

    result = await cactus_runner_client.get("/tm", headers={"ssl-client-cert": URI_ENCODED_CERT})
    await assert_success_response(result)

    result = await cactus_runner_client.get("/edev/1/der", headers={"ssl-client-cert": URI_ENCODED_CERT})
    await assert_success_response(result)

    # Post Mirror Usage Point
    xml_data_dir = Path(__file__).parent.parent / "data" / "xml"
    mup_xml = (xml_data_dir / "mup.xml").read_text().strip()
    mmr_xml = (xml_data_dir / "mmr.xml").read_text().strip()
    result = await cactus_runner_client.post(
        "/mup", data=mup_xml, headers={"ssl-client-cert": URI_ENCODED_CERT, "Content-Type": "application/sep+xml"}
    )
    await assert_success_response(result)
    location = result.headers.get("Location")
    mup_id = location.split("/")[-1]

    # Post Readings to Mirror Usage Point
    result = await cactus_runner_client.post(
        f"/mup/{mup_id}",
        data=mmr_xml,
        headers={"ssl-client-cert": URI_ENCODED_CERT, "Content-Type": "application/sep+xml"},
    )
    await assert_success_response(result)

    # TEST 1: List all request IDs
    result = await cactus_runner_client.get("/requests")
    await assert_success_response(result)

    requests_data = await result.json()

    assert "request_ids" in requests_data, "Response should contain 'request_ids' field"
    assert "count" in requests_data, "Response should contain 'count' field"
    assert isinstance(requests_data["request_ids"], list), "request_ids should be a list"

    request_ids = requests_data["request_ids"]
    count = requests_data["count"]

    # Verify count matches list length
    assert len(request_ids) == count, f"Count {count} should match length of request_ids list {len(request_ids)}"
    assert count >= 6, f"Should have at least 6 requests, got {count} (may have more from polling)"

    # Verify IDs start from 0
    assert request_ids[0] == 0, f"Request IDs should start from 0, got {request_ids[0]}"

    # Verify IDs are sorted
    assert request_ids == sorted(request_ids), "Request IDs should be sorted in ascending order"

    # Verify no duplicate IDs
    assert len(request_ids) == len(set(request_ids)), f"Request IDs should be unique (no duplicates)"

    # TEST 2: Retrieve first request (GET /dcap)
    result = await cactus_runner_client.get("/request/0")
    await assert_success_response(result)

    request_data = await result.json()

    assert request_data["request_id"] == 0, "Request ID should be 0"
    assert "request" in request_data, "Response should contain 'request' field"
    assert "response" in request_data, "Response should contain 'response' field"
    assert request_data["request"] is not None, "Request content should not be None"
    assert request_data["response"] is not None, "Response content should not be None"

    # Verify request format
    request_lines = request_data["request"].split("\n")
    assert request_lines[0] == "GET /dcap HTTP/1.1", f"First request should be GET /dcap, got: {request_lines[0]}"

    # Verify request contains headers
    assert any("Host:" in line for line in request_lines), "Request should contain Host header"
    assert any("ssl-client-cert:" in line for line in request_lines), "Request should contain ssl-client-cert header"

    # Verify response format
    response = request_data["response"]
    assert response.startswith("HTTP/1.1 200 OK\n"), f"Response should start with HTTP/1.1 200 OK, got: {response[:50]}"
    assert "content-type: application/sep+xml" in response.lower(), "Response should have XML content-type"
    assert "<DeviceCapability" in response, "Response should contain DeviceCapability XML"
    assert 'href="/dcap"' in response, "Response should contain dcap href"
    assert 'pollRate="60"' in response, "Response should contain pollRate"

    # TEST 3: Retrieve last request
    last_request_id = request_ids[-1]
    result = await cactus_runner_client.get(f"/request/{last_request_id}")
    await assert_success_response(result)

    last_request_data = await result.json()

    assert last_request_data["request_id"] == last_request_id, f"Request ID should be {last_request_id}"
    assert "request" in last_request_data, "Response should contain 'request' field"
    assert "response" in last_request_data, "Response should contain 'response' field"
    assert last_request_data["request"] is not None, "Request content should not be None"
    assert last_request_data["response"] is not None, "Response content should not be None"

    # Verify basic HTTP format
    last_request = last_request_data["request"]
    assert "HTTP/1.1" in last_request, "Request should contain HTTP/1.1"

    last_response = last_request_data["response"]
    assert last_response.startswith("HTTP/1.1"), "Response should start with HTTP/1.1"

    # TEST 4: Verify POST /mup request with XML body
    post_mup_found = False

    for req_id in request_ids:
        result = await cactus_runner_client.get(f"/request/{req_id}")
        await assert_success_response(result)
        data = await result.json()
        request_lines = data["request"].split("\n")

        if request_lines[0] == "POST /mup HTTP/1.1":
            post_mup_found = True
            post_request = data["request"]

            # Verify headers
            assert "Content-Type: application/sep+xml" in post_request, "POST should have XML content-type header"
            assert "Content-Length:" in post_request, "POST should have Content-Length header"

            # Verify body
            assert "\n\n" in post_request, "Request should have headers and body separated by blank line"
            headers_section, body = post_request.split("\n\n", 1)
            assert len(body) > 0, "POST request should have body content"
            assert "<MirrorUsagePoint xmlns=" in body, "Body should contain MirrorUsagePoint XML"
            assert "<mRID>0600006C</mRID>" in body, "Body should contain mRID element"
            assert "<description>Max Watts</description>" in body, "Body should contain description element"
            assert "</MirrorUsagePoint>" in body, "Body should have closing MirrorUsagePoint tag"

            # Verify response
            post_response = data["response"]
            assert post_response.startswith("HTTP/1.1 201 Created\n"), "POST response should be 201 Created"
            assert "location:" in post_response.lower(), "POST response should have Location header"
            assert "content-length: 0" in post_response.lower(), "POST response should have zero content-length"

            break

    assert post_mup_found, "Should have found POST /mup request in the request history"

    # TEST 5: Verify POST /mup/{id} request with readings
    post_mup_id_found = False

    for req_id in request_ids:
        result = await cactus_runner_client.get(f"/request/{req_id}")
        await assert_success_response(result)
        data = await result.json()
        request_lines = data["request"].split("\n")
        request_line = request_lines[0]

        # Look for POST /mup/{numeric_id} (not just POST /mup)
        if request_line.startswith("POST /mup/") and request_line != "POST /mup HTTP/1.1":
            post_mup_id_found = True
            post_mup_id_request = data["request"]

            # Verify headers
            assert (
                "Content-Type: application/sep+xml" in post_mup_id_request
            ), "POST should have XML content-type header"
            assert "Content-Length:" in post_mup_id_request, "POST should have Content-Length header"

            # Verify body with MMR content
            assert "\n\n" in post_mup_id_request, "Request should have headers and body separated by blank line"
            headers_section, body = post_mup_id_request.split("\n\n", 1)
            assert len(body) > 0, "POST request should have body content"
            assert "MirrorMeterReading" in body, "Body should contain MirrorMeterReading XML"
            assert "Reading" in body, "Body should contain Reading elements"

            # Verify response
            post_mup_id_response = data["response"]
            response_first_line = post_mup_id_response.split("\n")[0]
            assert response_first_line in [
                "HTTP/1.1 201 Created",
                "HTTP/1.1 204 No Content",
            ], f"POST response should be 201 or 204, got: {response_first_line}"

            break

    assert post_mup_id_found, f"Should have found POST /mup/{{id}} request with readings (mup_id={mup_id})"

    # TEST 6: Invalid request ID returns 404
    invalid_id = max(request_ids) + 1000
    result = await cactus_runner_client.get(f"/request/{invalid_id}")
    assert result.status == 404, f"Invalid request ID should return 404, got {result.status}"

    error_data = await result.json()
    assert "error" in error_data, "Error response should contain 'error' field"
    assert (
        f"Request data not found for ID: {invalid_id}" == error_data["error"]
    ), "Error message should match expected format"

    # TEST 7: Non-numeric request ID returns 400
    result = await cactus_runner_client.get("/request/invalid")
    assert result.status == 400, f"Non-numeric request ID should return 400, got {result.status}"

    error_data = await result.json()
    assert "error" in error_data, "Error response should contain 'error' field"
    assert error_data["error"] == "Invalid request_id parameter", "Error message should indicate invalid parameter"
