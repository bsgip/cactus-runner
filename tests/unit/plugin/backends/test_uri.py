import pytest
from envoy_schema.server.schema import uri as sep2_uri

from cactus_runner.plugin.backends.uri import (
    WILDCARD,
    ParsedUri,
    ResourceIndex,
    _parse_index,
    _uri_template_matches,
    identify_envoy_uri,
    parse_uri,
)


def test_resource_index_wildcard() -> None:
    """ResourceIndex.wildcard should create a wildcard resource reference."""
    result = ResourceIndex.wildcard()

    assert result.value is None
    assert result.is_wildcard is True


@pytest.mark.parametrize(
    ("component", "expected"),
    [
        ("1", ResourceIndex(value=1)),
        ("123", ResourceIndex(value=123)),
        (WILDCARD, ResourceIndex.wildcard()),
    ],
)
def test_parse_index_valid(component: str, expected: ResourceIndex) -> None:
    """_parse_index should recognise numeric and wildcard components."""
    assert _parse_index(component) == expected


@pytest.mark.parametrize(
    "component",
    [
        "",
        "abc",
        "1.0",
        "-1",
        "foo123",
    ],
)
def test_parse_index_invalid(component: str) -> None:
    """_parse_index should return None for unsupported components."""
    assert _parse_index(component) is None


def test_uri_template_matches_exact() -> None:
    """URI templates should match equivalent concrete URIs."""
    assert _uri_template_matches(
        "/edev/{site_id}/der/{der_id}/ders",
        "/edev/1/der/1/ders",
    )


def test_uri_template_matches_wildcard() -> None:
    """Wildcards should match URI template parameters."""
    assert _uri_template_matches(
        "/mup/{mup_id}",
        "/mup/*",
    )


def test_uri_template_matches_returns_false_when_component_count_differs() -> None:
    """Mismatched URI lengths should not match."""
    assert not _uri_template_matches(
        "/edev/{site_id}/der",
        "/edev/1/der/1",
    )


def test_uri_template_matches_returns_false_when_static_component_differs() -> None:
    """Different fixed path components should not match."""
    assert not _uri_template_matches(
        "/edev/{site_id}/der/{der_id}/ders",
        "/edev/1/der/1/derg",
    )


def test_identify_envoy_uri_der_status() -> None:
    """DER status URIs should resolve to the DERStatusUri template."""
    template, name = identify_envoy_uri("/edev/1/der/1/ders")

    assert template == sep2_uri.DERStatusUri
    assert name == "DERStatusUri"


def test_identify_envoy_uri_der_settings() -> None:
    """DER settings URIs should resolve to the DERSettingsUri template."""
    template, name = identify_envoy_uri("/edev/1/der/1/derg")

    assert template == sep2_uri.DERSettingsUri
    assert name == "DERSettingsUri"


def test_identify_envoy_uri_unknown() -> None:
    """Unknown URIs should not match any envoy-schema definition."""
    template, name = identify_envoy_uri("/this/does/not/exist")

    assert template is None
    assert name is None


def test_parse_uri_requires_absolute_path() -> None:
    """parse_uri should reject non-absolute paths."""
    with pytest.raises(ValueError, match="URI must begin with '/'"):
        parse_uri("edev/1/der/1/ders")


def test_parse_uri_der_status() -> None:
    """DER status URIs should be fully parsed into a ParsedUri."""
    result = parse_uri("/edev/1/der/1/ders")

    assert isinstance(result, ParsedUri)

    assert result.original_uri == "/edev/1/der/1/ders"

    assert result.components == (
        "edev",
        "1",
        "der",
        "1",
        "ders",
    )

    assert result.matched_uri_name == "DERStatusUri"
    assert result.matched_uri_template == sep2_uri.DERStatusUri

    assert result.end_device == ResourceIndex(value=1)
    assert result.der == ResourceIndex(value=1)

    assert result.fsa is None
    assert result.der_program is None
    assert result.tariff_profile is None
    assert result.mirror_usage_point is None


def test_parse_uri_der_program_fsa_list() -> None:
    """FSA-scoped DER program URIs should expose EndDevice and FSA indices."""
    result = parse_uri("/edev/1/fsa/5/derp")

    assert result.end_device == ResourceIndex(value=1)
    assert result.fsa == ResourceIndex(value=5)

    assert result.matched_uri_name == "DERProgramFSAListUri"
    assert result.matched_uri_template == sep2_uri.DERProgramFSAListUri


def test_parse_uri_tariff_profile() -> None:
    """Tariff profile URIs should expose the tariff profile index."""
    result = parse_uri("/edev/1/tp/7")

    assert result.end_device == ResourceIndex(value=1)
    assert result.tariff_profile == ResourceIndex(value=7)


def test_parse_uri_der_program() -> None:
    """DER program URIs should expose the DER program index."""
    result = parse_uri("/edev/1/derp/9")

    assert result.end_device == ResourceIndex(value=1)
    assert result.der_program == ResourceIndex(value=9)


def test_parse_uri_mirror_usage_point_wildcard() -> None:
    """Wildcard MUP URIs should be represented as wildcard ResourceIndex values."""
    result = parse_uri("/mup/*")

    assert result.mirror_usage_point == ResourceIndex.wildcard()

    assert result.matched_uri_name == "MirrorUsagePointUri"
    assert result.matched_uri_template == sep2_uri.MirrorUsagePointUri


def test_parse_uri_mirror_usage_point_index() -> None:
    """Concrete MUP URIs should expose the MUP identifier."""
    result = parse_uri("/mup/42")

    assert result.mirror_usage_point == ResourceIndex(value=42)

    assert result.matched_uri_name == "MirrorUsagePointUri"
    assert result.matched_uri_template == sep2_uri.MirrorUsagePointUri


def test_parse_uri_unknown_uri_still_extracts_indices() -> None:
    """Unknown URIs should still expose recognised resource indices."""
    result = parse_uri("/edev/1/custom/123")

    assert result.matched_uri_name is None
    assert result.matched_uri_template is None

    assert result.end_device == ResourceIndex(value=1)
