from dataclasses import dataclass

from envoy_schema.server.schema import uri as sep2_uri

WILDCARD = "*"


@dataclass(frozen=True)
class ResourceIndex:
    """Represents an indexed resource reference extracted from a CACTUS URI.

    Examples:
        /edev/1/der/1/ders

        results in:

            end_device=ResourceIndex(value=1)
            der=ResourceIndex(value=1)

        /mup/*

        results in:

            mirror_usage_point=ResourceIndex(is_wildcard=True)
    """

    value: int | None = None
    is_wildcard: bool = False

    @classmethod
    def wildcard(cls) -> "ResourceIndex":
        """Construct a wildcard resource reference."""
        return cls(value=None, is_wildcard=True)


@dataclass(frozen=True)
class ParsedUri:
    """Structured representation of a CACTUS endpoint.

    Test definitions currently reference Envoy-style SEP2 paths. This class
    exposes commonly-used indexed resources while also identifying the
    matching envoy-schema URI template.

    Examples:
        /edev/1/der/1/ders

        becomes:

            ParsedUri(
                original_uri="/edev/1/der/1/ders",
                matched_uri_name="DERStatusUri",
                matched_uri_template="/edev/{site_id}/der/{der_id}/ders",
                end_device=ResourceIndex(value=1),
                der=ResourceIndex(value=1),
            )

        /mup/*

        becomes:

            ParsedUri(
                original_uri="/mup/*",
                matched_uri_name="MirrorUsagePointUri",
                matched_uri_template="/mup/{mup_id}",
                mirror_usage_point=ResourceIndex(is_wildcard=True),
            )
    """

    original_uri: str

    components: tuple[str, ...]

    matched_uri_name: str | None = None
    matched_uri_template: str | None = None

    end_device: ResourceIndex | None = None
    der: ResourceIndex | None = None
    fsa: ResourceIndex | None = None
    tariff_profile: ResourceIndex | None = None
    der_program: ResourceIndex | None = None
    mirror_usage_point: ResourceIndex | None = None


def _parse_index(component: str) -> ResourceIndex | None:
    """Convert a URI component into a ResourceIndex if applicable."""
    if component == WILDCARD:
        return ResourceIndex.wildcard()

    if component.isdigit():
        return ResourceIndex(value=int(component))

    return None


def _uri_template_matches(template: str, uri: str) -> bool:
    """Return True if a URI matches an envoy-schema URI template.

    Example:

        template = "/edev/{site_id}/der/{der_id}/ders"
        uri = "/edev/1/der/1/ders"

    returns True.
    """
    if not template:
        return False

    template_components = tuple(filter(None, template.split("/")))
    uri_components = tuple(filter(None, uri.split("/")))

    if len(template_components) != len(uri_components):
        return False

    for template_component, uri_component in zip(
        template_components,
        uri_components,
        strict=True,
    ):
        if template_component.startswith("{") and template_component.endswith("}"):
            continue

        if uri_component == WILDCARD:
            continue

        if template_component != uri_component:
            return False

    return True


def identify_envoy_uri(uri: str) -> tuple[str | None, str | None]:
    """Identify the envoy-schema URI definition that matches a URI.

    Returns:
        Tuple of:

        (
            uri_template,
            constant_name,
        )

    Example:

        /edev/1/der/1/ders

        returns:

        (
            "/edev/{site_id}/der/{der_id}/ders",
            "DERStatusUri",
        )
    """
    for name in dir(sep2_uri):
        if not name.endswith("Uri"):
            continue

        template = getattr(sep2_uri, name)

        if not isinstance(template, str):
            continue

        if not template:
            continue

        if _uri_template_matches(template, uri):
            return (template, name)

    return (None, None)


def parse_uri(uri: str) -> ParsedUri:
    """Parse a CACTUS endpoint into a structured ParsedUri.

    The resulting object contains:

    - The original URI.
    - All URI components.
    - Extracted resource indices.
    - The matching envoy-schema URI template (if identifiable).

    Args:
        uri: Endpoint path from a test definition.

    Raises:
        ValueError: If the URI is not absolute.
    """
    if not uri.startswith("/"):
        raise ValueError(f"URI must begin with '/': {uri}")

    components = tuple(filter(None, uri.split("/")))

    matched_uri_template, matched_uri_name = identify_envoy_uri(uri)

    end_device: ResourceIndex | None = None
    der: ResourceIndex | None = None
    fsa: ResourceIndex | None = None
    tariff_profile: ResourceIndex | None = None
    der_program: ResourceIndex | None = None
    mirror_usage_point: ResourceIndex | None = None

    for idx in range(len(components) - 1):
        resource_name = components[idx]
        resource_value = components[idx + 1]

        resource_index = _parse_index(resource_value)

        if resource_index is None:
            continue

        match resource_name:
            case "edev":
                end_device = resource_index

            case "der":
                der = resource_index

            case "fsa":
                fsa = resource_index

            case "tp":
                tariff_profile = resource_index

            case "derp":
                der_program = resource_index

            case "mup":
                mirror_usage_point = resource_index

    return ParsedUri(
        original_uri=uri,
        components=components,
        matched_uri_name=matched_uri_name,
        matched_uri_template=matched_uri_template,
        end_device=end_device,
        der=der,
        fsa=fsa,
        tariff_profile=tariff_profile,
        der_program=der_program,
        mirror_usage_point=mirror_usage_point,
    )
