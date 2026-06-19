from unittest.mock import Mock

import pytest
from aiohttp.web import Request

from cactus_runner.app.uri import MountedProxyPathParts, does_endpoint_match, uri_path_join, uri_proxy_path_extract


@pytest.mark.parametrize(
    "path, match, expected",
    [
        ("/foo", "/foo", True),
        ("/foo", "/bar", False),
        ("/edev/123", "/edev/123", True),
        ("/edev/123", "/edev/193", False),
        ("/edev/123", "/edev/foo/123", False),
        ("/edev/123", "/blah/123", False),
        # Test wildcards
        ("/foo", "*", True),  # '*' matches a single path component
        ("/foo", "/*", True),
        ("/foo/123", "/*", False),
        ("/foo/123", "/*/*", True),
        ("/foo/123", "/*/123", True),
        ("/foo/123", "/foo/*", True),
        ("/foo/123", "/bar/*", False),
        ("/foo/123", "/foo/*/bar", False),
        ("/foo/123/bar", "/foo/*/bar", True),
        ("/foo/123/bar", "/foo/*/*", True),
        ("/foo/123/bar", "/*/*/*", True),
        ("/foo/123/bar", "/baz/*/*/*", False),
        ("/bar/123/bar", "/foo/*/*", False),
        ("/edev/123/derp/1", "/edev/*/derp/1", True),
        ("/edev/123/derp/1", "/edev/1*3/derp/1", False),  # partial matches not supported
        ("/foo", "/edev/*/derp/1", False),
        ("/derp/1", "/edev/*/derp/1", False),
    ],
)
def test_does_endpoint_match(path: str, match: str, expected: bool):
    actual = does_endpoint_match(path, match)
    assert isinstance(actual, bool)
    assert actual is expected


@pytest.mark.parametrize(
    "parts, expected",
    [
        ([], ""),
        (["foo"], "foo"),
        (["/foo/"], "/foo"),
        (["/", "/", "/foo"], "/foo"),
        (["/", "/", "foo"], "/foo"),
        (["/", "/", "", "1", "", "a", "/", "B", "foo"], "/1/a/B/foo"),
        (["/foo/", "/", "/", "/", "", "", "bar"], "/foo/bar"),
        (["/foo/", "/", "/", "/", "", "", "/bar"], "/foo/bar"),
        (["http://example.com"], "http://example.com"),
        (["http://example.com/"], "http://example.com"),
        (["http://example.com", "1", "2"], "http://example.com/1/2"),
        (["http://example.com/", "/path", "part", "/foo/", "/1/"], "http://example.com/path/part/foo/1"),
    ],
)
def test_uri_path_join(parts: list[str], expected: str):
    actual = uri_path_join(*parts)
    assert isinstance(actual, str)
    assert actual == expected


@pytest.mark.parametrize(
    "mount_point, proxy_prefix, input_path_qs, expected_path, expected_path_qs",
    [
        ("/", "/", "/edev/123/derp", "/edev/123/derp", "/edev/123/derp"),
        ("/", "/", "/edev/123/derp?l=1&s=2", "/edev/123/derp", "/edev/123/derp?l=1&s=2"),
        ("/", "/envoy", "/envoy/edev/123/derp?l=1&s=2", "/edev/123/derp", "/edev/123/derp?l=1&s=2"),
        ("/foo", "/bar", "/foo/bar/edev/123/derp?l=1&s=2", "/edev/123/derp", "/edev/123/derp?l=1&s=2"),
        ("/foo", "/bar", "/foo/bar/dcap", "/dcap", "/dcap"),
        ("/fooDNE", "/barDNE", "/foo/bar/dcap", "/foo/bar/dcap", "/foo/bar/dcap"),  # Don't strip if we don't find
        ("/foo", "/barDNE", "/foo/bar/dcap", "/bar/dcap", "/bar/dcap"),  # Don't strip if we don't find
        ("/fooDNE", "/bar", "/foo/bar/dcap", "/foo/bar/dcap", "/foo/bar/dcap"),  # Don't strip if we don't find
    ],
)
def test_uri_proxy_path_extract(
    mount_point: str, proxy_prefix: str, input_path_qs: str, expected_path: str, expected_path_qs: str
):
    mock_request = Mock(Request)
    mock_request.path = input_path_qs.split("?")[0]
    mock_request.path_qs = input_path_qs

    actual = uri_proxy_path_extract(mount_point, proxy_prefix, mock_request)
    assert isinstance(actual, MountedProxyPathParts)
    assert actual.mount_point == mount_point
    assert actual.proxy_prefix == proxy_prefix
    assert actual.path == expected_path
    assert actual.path_qs == expected_path_qs
