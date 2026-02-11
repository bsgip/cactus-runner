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
    WILDCARD = "*"
    if WILDCARD not in match:
        return path == match

    # Otherwise we need to do a component by component comparison
    request_components = list(filter(None, path.split("/")))  # Remove empty strings
    match_components = list(filter(None, match.split("/")))  # Remove empty strings

    # Must have same number of components for a match
    if len(request_components) != len(match_components):
        return False

    # Compare each component
    for request_component, match_component in zip(request_components, match_components):
        if match_component != WILDCARD and request_component != match_component:
            return False

    return True
