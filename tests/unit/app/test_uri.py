import pytest

from cactus_runner.app.uri import does_endpoint_match


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
