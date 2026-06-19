from aiohttp.web import Request
from attr import dataclass

WILDCARD = "*"


def does_endpoint_match(path: str, match: str) -> bool:
    """Performs all logic for matching an "endpoint" to an incoming request's path.

    '*' can be a "wildcard" character for matching a single component of the path (a path component is part of the path
    seperated by '/'). It will NOT partially match

    eg:
    match=/edev/*/derp/1  would match /edev/123/derp/1
    match=/edev/1*3/derp/1  would NOT match /edev/123/derp/1

    NOTE: This function expects paths WITHOUT any mount point prefix - those should be stripped before calling.
    """

    # If we don't have a wildcard - do an EXACT match
    if WILDCARD not in match:
        return path == match

    # Otherwise we need to do a component by component comparison
    request_components = list(filter(None, path.split("/")))  # Remove empty strings
    match_components = list(filter(None, match.split("/")))  # Remove empty strings

    # Must have same number of components for a match
    if len(request_components) != len(match_components):
        return False

    # Compare each component
    for request_component, match_component in zip(request_components, match_components, strict=True):
        if match_component != WILDCARD and request_component != match_component:
            return False

    return True


def uri_path_join(*parts: str) -> str:
    """Given a series of path components, join them with '/' characters such that a doubled '/' is not included."""
    if not parts:
        return ""

    first = parts[0].rstrip("/")
    rest = "/".join(part.strip("/") for part in parts[1:] if part and part != "/")
    return f"{first}/{rest}" if rest else first


@dataclass
class MountedProxyPathParts:
    """Given an incoming request like /runner/envoy/edev/123/derp splits it out into the mount_point, proxy_prefix and
    downstream proxy path"""

    mount_point: str  # Given /runner/envoy/edev/123/derp - This would be /runner
    proxy_prefix: str  # Given /runner/envoy/edev/123/derp - This would be /envoy
    path: str  # The path of the request (sans mount point / proxy prefix) - NO query string. eg: /edev/123/derp
    path_qs: str  # Similar to path but also includes the query string. eg: /edev/123/derp?l=100&s=0


def uri_proxy_path_extract(mount_point: str, proxy_prefix: str, request: Request) -> MountedProxyPathParts:
    """Given a static mount point and proxy prefix, decompose the request_path into the constituent parts"""

    path = request.path
    path_qs = request.path_qs
    if mount_point != "/":
        if path.startswith(mount_point):
            path = path[len(mount_point) :]

        if path_qs.startswith(mount_point):
            path_qs = path_qs[len(mount_point) :]

    if proxy_prefix != "/":
        if path.startswith(proxy_prefix):
            path = path[len(proxy_prefix) :]

        if path_qs.startswith(proxy_prefix):
            path_qs = path_qs[len(proxy_prefix) :]

    return MountedProxyPathParts(
        mount_point=mount_point,
        proxy_prefix=proxy_prefix,
        path=path,
        path_qs=path_qs,
    )
