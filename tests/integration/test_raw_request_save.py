from pathlib import Path
from cactus_test_definitions import CSIPAusVersion
from fastapi.testclient import TestClient
import pytest
from tests.integration.test_all_01 import URI_ENCODED_CERT, assert_success_response


@pytest.mark.slow
@pytest.mark.anyio
async def test_request_data_retrieval_endpoints(cactus_runner_client: TestClient, pg_empty_config):
    """Test retrieval of raw request/response data via /requests and /request/{request_id} endpoints"""

    # Arrange: Make a few requests to generate test data
    xml_data_dir = Path(__file__).parent.parent / "data" / "xml"
    mup_xml = (xml_data_dir / "mup.xml").read_text().strip()
    mmr_xml = (xml_data_dir / "mmr.xml").read_text().strip()

    result = await cactus_runner_client.post(
        f"/init?test=ALL-01&device_certificate={URI_ENCODED_CERT}&csip_aus_version={CSIPAusVersion.RELEASE_1_2.value}"
    )
    await assert_success_response(result)

    # GET /dcap
    result = await cactus_runner_client.get("/dcap", headers={"ssl-client-cert": URI_ENCODED_CERT})
    await assert_success_response(result)

    # POST /mup
    result = await cactus_runner_client.post(
        "/mup", data=mup_xml, headers={"ssl-client-cert": URI_ENCODED_CERT, "Content-Type": "application/sep+xml"}
    )
    await assert_success_response(result)
    location = result.headers.get("Location")
    mup_id = location.split("/")[-1]

    # POST /mup/{mup_id}
    result = await cactus_runner_client.post(
        f"/mup/{mup_id}",
        data=mmr_xml,
        headers={"ssl-client-cert": URI_ENCODED_CERT, "Content-Type": "application/sep+xml"},
    )
    await assert_success_response(result)

    # TEST: Get list of all requests
    result = await cactus_runner_client.get("/requests")
    await assert_success_response(result)

    requests_data = await result.json()
    assert "request_ids" in requests_data and "count" in requests_data
    assert requests_data["count"] >= 3, f"Should have at least 3 requests, got {requests_data['count']}"
    assert len(requests_data["request_ids"]) == requests_data["count"]
    request_ids = requests_data["request_ids"]
    assert request_ids == sorted(request_ids), "Request IDs should be sorted"

    # Find explicit requests
    dcap_request_id = None
    mup_post_request_id = None
    mup_update_request_id = None

    for req_id in request_ids:
        result = await cactus_runner_client.get(f"/request/{req_id}")
        await assert_success_response(result)
        req_data = await result.json()
        request_line = req_data["request"].split("\n")[0]

        if "GET /dcap" in request_line:
            dcap_request_id = req_id
        elif request_line.startswith("POST /mup/"):
            mup_update_request_id = req_id
        elif "POST /mup" in request_line and not request_line.startswith("POST /mup/"):
            mup_post_request_id = req_id

    assert dcap_request_id is not None
    assert mup_post_request_id is not None
    assert mup_update_request_id is not None

    # TEST: Verify structure using POST /mup
    result = await cactus_runner_client.get(f"/request/{mup_post_request_id}")
    await assert_success_response(result)
    post_data = await result.json()
    assert post_data["request_id"] == mup_post_request_id
    assert "request" in post_data and "response" in post_data

    # Verify request format: "POST /mup HTTP/1.1\nHeaders...\n\nBody..."
    post_request = post_data["request"]
    assert post_request.startswith("POST /mup HTTP/1.1\n")

    request_parts = post_request.split("\n\n", 1)
    assert len(request_parts) == 2
    headers_part, body_part = request_parts

    assert "ssl-client-cert:" in headers_part.lower() and "content-type:" in headers_part.lower()
    assert "application/sep+xml" in headers_part
    assert "MirrorUsagePoint" in body_part and len(body_part) > 0

    # Verify response format: "HTTP/1.1 201 Created\nHeaders...\n\nBody..."
    post_response = post_data["response"]
    assert post_response.startswith("HTTP/1.1 201")
    assert "location:" in post_response.lower()

    # TEST: Invalid request ID (should return 404)
    invalid_id = max(request_ids) + 1000
    result = await cactus_runner_client.get(f"/request/{invalid_id}")
    assert result.status == 404
    error_data = await result.json()
    assert "error" in error_data

    # TEST: Non-numeric request ID (should return 400)
    result = await cactus_runner_client.get("/request/invalid")
    assert result.status == 400
    error_data = await result.json()
    assert "error" in error_data
