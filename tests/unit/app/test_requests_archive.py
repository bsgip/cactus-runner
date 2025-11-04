from assertical.fake.generator import generate_class_instance
from http import HTTPStatus, HTTPMethod
from datetime import datetime, timezone
from aiohttp import web
from multidict import CIMultiDict
import pytest
from cactus_runner.app.requests_archive import write_request_response_files, read_request_response_files
from cactus_runner.models import RequestEntry, RequestData
from cactus_runner.app.proxy import ProxyResult


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
        timestamp=datetime.now(timezone.utc),
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
    assert "POST /dcap HTTP/1.1" in request_data.request
    assert "Host: localhost" in request_data.request
    assert "Content-Type: application/xml" in request_data.request
    assert "<RequestBody>test data</RequestBody>" in request_data.request

    # Verify response file content
    assert "HTTP/1.1 200 OK" in request_data.response
    assert "Content-Type: application/xml" in request_data.response
    assert "<ResponseBody>response data</ResponseBody>" in request_data.response


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
        request_body=None,
        request_encoding=None,
        request_headers=CIMultiDict({}),
        response=response,
    )

    entry = RequestEntry(
        url="http://localhost:8000/test",
        path="/test",
        method=HTTPMethod.GET,
        status=HTTPStatus.OK,
        timestamp=datetime.now(timezone.utc),
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
