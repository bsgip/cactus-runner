from pathlib import Path
from cactus_test_definitions import CSIPAusVersion
from fastapi.testclient import TestClient
import pytest
from tests.integration.test_all_01 import URI_ENCODED_CERT, assert_success_response


@pytest.mark.slow
@pytest.mark.anyio
async def test_request_data_retrieval_endpoints(cactus_runner_client: TestClient, pg_empty_config):
    """Test retrieval of raw request/response data via /requests and /request/{request_id} endpoints"""

    # SETUP: Run ALL-01 workflow to generate request/response data
    result = await cactus_runner_client.post(
        f"/init?test=ALL-01&device_certificate={URI_ENCODED_CERT}&csip_aus_version={CSIPAusVersion.RELEASE_1_2.value}"
    )
    await assert_success_response(result)

    headers = {"ssl-client-cert": URI_ENCODED_CERT}
    for endpoint in ["/dcap", "/edev?s=0&l=100", "/tm", "/edev/1/der"]:
        await assert_success_response(await cactus_runner_client.get(endpoint, headers=headers))

    # Post Mirror Usage Point and readings
    xml_data_dir = Path(__file__).parent.parent / "data" / "xml"
    xml_headers = {**headers, "Content-Type": "application/sep+xml"}

    result = await cactus_runner_client.post(
        "/mup", data=(xml_data_dir / "mup.xml").read_text().strip(), headers=xml_headers
    )
    await assert_success_response(result)
    mup_id = result.headers.get("Location").split("/")[-1]  # Get mup id for next post

    await assert_success_response(
        await cactus_runner_client.post(
            f"/mup/{mup_id}", data=(xml_data_dir / "mmr.xml").read_text().strip(), headers=xml_headers
        )
    )

    # TEST 1: List all request IDs
    result = await cactus_runner_client.get("/requests")
    await assert_success_response(result)
    requests_data = await result.json()
    request_ids = requests_data["request_ids"]
    count = requests_data["count"]

    assert isinstance(requests_data["request_ids"], list)
    assert len(request_ids) == count == len(set(request_ids)), "Count mismatch or duplicate IDs"
    assert count >= 6
    assert request_ids[0] == 0
    assert request_ids == sorted(request_ids)

    # TEST 2: Retrieve first request (GET /dcap)
    request_data = await (await cactus_runner_client.get("/request/0")).json()

    request_lines = request_data["request"].split("\n")
    assert request_data["request_id"] == 0
    assert request_lines[0] == "GET /dcap HTTP/1.1"
    assert any("Host:" in line for line in request_lines)
    assert any("ssl-client-cert:" in line for line in request_lines)

    response = request_data["response"]
    assert response.startswith("HTTP/1.1 200 OK\n")
    assert "content-type: application/sep+xml" in response.lower()
    assert all(s in response for s in ["<DeviceCapability", 'href="/dcap"', 'pollRate="60"'])

    # TEST 4: Verify POST /mup/{id} request
    post_mup_id_found = False
    for req_id in request_ids:
        data = await (await cactus_runner_client.get(f"/request/{req_id}")).json()
        request_line = data["request"].split("\n")[0]
        if request_line.startswith("POST /mup/"):
            post_mup_id_found = True
            headers_section, body = data["request"].split("\n\n", 1)
            assert "Content-Type: application/sep+xml" in data["request"]
            assert "MirrorMeterReading" in body
            assert data["response"].split("\n")[0] in ["HTTP/1.1 201 Created", "HTTP/1.1 204 No Content"]
            break
    assert post_mup_id_found

    # TEST 5: Invalid request ID returns 404
    invalid_id = max(request_ids) + 1000
    result = await cactus_runner_client.get(f"/request/{invalid_id}")
    assert result.status == 404
    assert (await result.json())["error"] == f"Request data not found for ID: {invalid_id}"

    # TEST 6: Non-numeric request ID returns 400
    result = await cactus_runner_client.get("/request/invalid")
    assert result.status == 400
    assert (await result.json())["error"] == "Invalid request_id parameter"
