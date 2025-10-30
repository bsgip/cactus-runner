from unittest import mock
from unittest.mock import patch
from http import HTTPStatus, HTTPMethod
from datetime import datetime, timezone
from aiohttp import web
from multidict import CIMultiDict
import pytest

from cactus_runner.app.save_requests import write_request_response_files
from cactus_runner.models import RequestEntry
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


def test_write_request_response_files_success_with_text_bodies(tmp_path_factory, proxy_result, entry):
    """Check we can write request/response files with text bodies successfully"""
    # Arrange
    temp_dir = tmp_path_factory.mktemp("request_data")

    # Act
    with patch("cactus_runner.app.save_requests.REQUEST_DATA_DIR", temp_dir):
        write_request_response_files(request_id=0, proxy_result=proxy_result, entry=entry)

    # Assert
    request_file = temp_dir / "000-ALL-01-001-dcap.request"
    response_file = temp_dir / "000-ALL-01-001-dcap.response"

    assert request_file.exists(), "Request file should be created"
    assert response_file.exists(), "Response file should be created"

    # Verify request file content
    with open(request_file, "r", encoding="utf-8") as f:
        request_content = f.read()

    assert "POST /dcap HTTP/1.1" in request_content
    assert "Host: localhost" in request_content
    assert "Content-Type: application/xml" in request_content
    assert "<RequestBody>test data</RequestBody>" in request_content

    # Verify response file content
    with open(response_file, "r", encoding="utf-8") as f:
        response_content = f.read()

    assert "HTTP/1.1 200 OK" in response_content
    assert "Content-Type: application/xml" in response_content
    assert "<ResponseBody>response data</ResponseBody>" in response_content


def test_write_request_response_files_with_binary_request_body(tmp_path_factory, proxy_result, entry):
    """Check that binary request bodies that can't be decoded are handled gracefully"""
    # Arrange
    temp_dir = tmp_path_factory.mktemp("request_data")

    # Binary data that will fail UTF-8 decoding
    proxy_result.request_body = b"\x80\x81\x82\x83\xff\xfe"
    entry.path = "/dcap"

    # Act
    with patch("cactus_runner.app.save_requests.REQUEST_DATA_DIR", temp_dir):
        write_request_response_files(request_id=5, proxy_result=proxy_result, entry=entry)

    # Assert
    request_files = list(temp_dir.glob("005-*.request"))

    assert len(request_files) == 1, f"Expected 1 request file, found {len(request_files)}"
    request_file = request_files[0]

    with open(request_file, "r", encoding="utf-8") as f:
        request_content = f.read()

    assert "POST /dcap HTTP/1.1" in request_content
    assert "�" in request_content, "Binary body should contain replacement characters"


def test_write_request_response_files_creates_directory_if_missing(tmp_path_factory):
    """Check that the request data directory is created if it doesn't exist"""
    # Arrange
    temp_base = tmp_path_factory.mktemp("base")
    temp_dir = temp_base / "nonexistent_dir"

    assert not temp_dir.exists(), "Directory should not exist initially"

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
    with patch("cactus_runner.app.save_requests.REQUEST_DATA_DIR", temp_dir):
        write_request_response_files(request_id=0, proxy_result=proxy_result, entry=entry)

    # Assert
    assert temp_dir.exists(), "Directory should be created"
    assert (temp_dir / "000-TEST-001-test.request").exists()
    assert (temp_dir / "000-TEST-001-test.response").exists()


def test_write_request_response_files_handles_write_failure_silently(tmp_path_factory, caplog):
    """Check that file write failures are logged but don't raise exceptions"""
    # Arrange
    temp_dir = tmp_path_factory.mktemp("request_data")

    response = web.Response(status=200, body=b"test")

    proxy_result = ProxyResult(
        uri="/test",
        request_method="GET",
        request_body=b"test",
        request_encoding="utf-8",
        response=response,
        request_headers=mock.MagicMock(),
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

    with patch("cactus_runner.app.save_requests.REQUEST_DATA_DIR", temp_dir):
        with patch("builtins.open", side_effect=PermissionError("Access denied")):
            # Should not raise exception
            write_request_response_files(request_id=0, proxy_result=proxy_result, entry=entry)

    assert "Failed to write request/response files for request_id=0" in caplog.text
    assert not (temp_dir / "000-TEST-001-test.request").exists()
