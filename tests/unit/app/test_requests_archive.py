from datetime import UTC, datetime
from http import HTTPMethod, HTTPStatus

import pytest
from aiohttp import web
from assertical.fake.generator import generate_class_instance
from cactus_schema.runner import RequestData, RequestEntry
from multidict import CIMultiDict

from cactus_runner.app.proxy import ProxyResult
from cactus_runner.app.requests_archive import (
    parse_request_start_header,
    prune_old_request_response_pairs,
    read_request_response_files,
    write_request_response_files,
)


@pytest.fixture
def proxy_result():
    request_body = b"<RequestBody>test data</RequestBody>"
    response_body = b"<ResponseBody>response data</ResponseBody>"

    response = web.Response(
        status=200,
        body=response_body,
        headers={"Content-Type": "application/xml", "Content-Length": str(len(response_body))},
    )

    result = ProxyResult(
        uri="/dcap",
        request_method="POST",
        request_body=request_body,
        request_encoding="utf-8",
        request_headers=CIMultiDict({"Host": "localhost", "Content-Type": "application/xml"}),
        response=response,
    )

    return result


@pytest.fixture
def entry():
    entry = RequestEntry(
        url="http://localhost:8000/dcap",
        path="/dcap",
        method=HTTPMethod.POST,
        status=HTTPStatus.OK,
        timestamp=datetime.now(UTC),
        step_name="ALL-01-001",
        body_xml_errors=[],
        request_id=0,
    )
    return entry


def test_write_request_response_files_success_with_text_bodies(proxy_result, entry):
    """Check we can write request/response files with text bodies successfully"""
    # Act
    request_id = 100
    write_request_response_files(request_id=request_id, proxy_result=proxy_result, entry=entry)
    request_content, response_content = read_request_response_files(request_id)

    # Assert
    request_data = RequestData(request_id=request_id, request=request_content, response=response_content)

    assert request_data.request is not None, "Request content should exist"
    assert request_data.response is not None, "Response content should exist"

    # Verify request file content
    assert f"# Epoch: {entry.timestamp.timestamp()}" in request_data.request
    assert f"# UTC: {entry.timestamp.strftime('%Y-%m-%dT%H:%M:%S.%fZ')}" in request_data.request
    assert "# Time source: runner (request received)" in request_data.request
    assert "POST /dcap HTTP/1.1" in request_data.request
    assert "Host: localhost" in request_data.request
    assert "Content-Type: application/xml" in request_data.request
    assert "<RequestBody>test data</RequestBody>" in request_data.request

    # Verify response file content
    assert f"# Epoch: {proxy_result.response_timestamp.timestamp()}" in request_data.response
    assert f"# UTC: {proxy_result.response_timestamp.strftime('%Y-%m-%dT%H:%M:%S.%fZ')}" in request_data.response
    assert "# Time source: runner (envoy response received)" in request_data.response
    assert "HTTP/1.1 200 OK" in request_data.response
    assert "Content-Type: application/xml" in request_data.response
    assert "<ResponseBody>response data</ResponseBody>" in request_data.response


@pytest.mark.parametrize(
    "raw_value, expected",
    [
        ("t=1752537600.123", datetime.fromtimestamp(1752537600.123, tz=UTC)),
        ("1752537600.123", datetime.fromtimestamp(1752537600.123, tz=UTC)),
        ("t=abc", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_request_start_header(raw_value, expected):
    headers = CIMultiDict({} if raw_value is None else {"X-Request-Start": raw_value})
    assert parse_request_start_header(headers) == expected


def test_write_request_response_files_prefers_nginx_request_time(proxy_result, entry):
    """The nginx X-Request-Start time should be used over entry.timestamp when present"""
    proxy_result.request_headers["X-Request-Start"] = "t=1752537600.123"
    nginx_timestamp = datetime.fromtimestamp(1752537600.123, tz=UTC)

    # Act
    request_id = 104
    write_request_response_files(request_id=request_id, proxy_result=proxy_result, entry=entry)
    request_content, response_content = read_request_response_files(request_id)
    assert request_content is not None
    assert response_content is not None

    # Assert
    assert f"# Epoch: {nginx_timestamp.timestamp()}" in request_content
    assert f"# UTC: {nginx_timestamp.strftime('%Y-%m-%dT%H:%M:%S.%fZ')}" in request_content
    assert "# Time source: nginx ingress" in request_content
    assert f"# Epoch: {entry.timestamp.timestamp()}" not in request_content
    # Response file still uses the runner-side response timestamp
    assert f"# Epoch: {proxy_result.response_timestamp.timestamp()}" in response_content


def test_write_request_response_files_with_binary_request_body(proxy_result, entry):
    """Check that binary request bodies that can't be decoded are handled gracefully"""
    # Binary data that will fail UTF-8 decoding
    proxy_result.request_body = b"\x80\x81\x82\x83\xff\xfe"
    entry.path = "/dcap"

    # Act
    request_id = 101
    write_request_response_files(request_id=request_id, proxy_result=proxy_result, entry=entry)
    request_content, response_content = read_request_response_files(request_id)

    # Assert
    request_data = RequestData(request_id=request_id, request=request_content, response=response_content)

    assert request_data.request is not None
    assert "POST /dcap HTTP/1.1" in request_data.request
    assert "�" in request_data.request, "Binary body should contain replacement characters"


def test_write_request_response_files_creates_directory_if_missing():
    """Check that the request data directory is created if it doesn't exist"""
    response = web.Response(status=200, body=b"test")

    proxy_result = ProxyResult(
        uri="/test",
        request_method="GET",
        request_body=bytes([]),
        request_encoding=None,
        request_headers=CIMultiDict({}),
        response=response,
    )

    entry = RequestEntry(
        url="http://localhost:8000/test",
        path="/test",
        method=HTTPMethod.GET,
        status=HTTPStatus.OK,
        timestamp=datetime.now(UTC),
        step_name="TEST-001",
        body_xml_errors=[],
        request_id=0,
    )

    # Act
    request_id = 102
    write_request_response_files(request_id=request_id, proxy_result=proxy_result, entry=entry)
    request_content, response_content = read_request_response_files(request_id)

    # Assert
    assert request_content is not None
    assert response_content is not None


def test_write_request_response_files_fails_without_raising_exceptions():
    response = web.Response(status=200, body=b"test")
    proxy_result = ProxyResult(
        uri="/test",
        request_method="GET",
        request_body=b"test",
        request_encoding="utf-8",
        response=response,
        request_headers=CIMultiDict({}),
    )
    entry = generate_class_instance(RequestEntry)

    # Just verify it doesn't raise
    write_request_response_files(request_id=103, proxy_result=proxy_result, entry=entry)


def test_prune_old_request_response_pairs_deletes_old_pair(proxy_result, entry):
    max_pairs = 3
    # Write 4 pairs
    for i in range(4):
        write_request_response_files(request_id=200 + i, proxy_result=proxy_result, entry=entry)
        prune_old_request_response_pairs(current_request_id=200 + i, max_pairs=max_pairs)

    # First pair should have been deleted
    request_content, response_content = read_request_response_files(200)
    assert request_content is None
    assert response_content is None

    # Last three should still exist
    for i in (201, 202, 203):
        req, resp = read_request_response_files(i)
        assert req is not None, f"request {i} should still exist"
        assert resp is not None, f"response {i} should still exist"


def test_prune_old_request_response_pairs_do_n0thing_below_limit(proxy_result, entry):
    write_request_response_files(request_id=210, proxy_result=proxy_result, entry=entry)
    prune_old_request_response_pairs(current_request_id=210, max_pairs=5000)

    req, resp = read_request_response_files(210)
    assert req is not None
    assert resp is not None
