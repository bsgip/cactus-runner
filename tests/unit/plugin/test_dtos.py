from dataclasses import fields

from cactus_runner.app.check import (
    DER_MODES_EQUIVALENTS,
    DER_VALUE_EQUIVALENTS,
    ParamsDERCapabilityContents,
    ParamsDERSettingsContents,
    SiteReadingTypeProperty,
)
from cactus_runner.plugin import dtos


def _fields_referenced_by_params(
    params_cls: type[ParamsDERCapabilityContents] | type[ParamsDERSettingsContents],
) -> set[str]:
    """Derives all field names that a Params class accesses on its corresponding DTO.

    Three access patterns are used in check.py:
      1. Direct attribute access (e.g. der_settings.grad_w) — caller adds these explicitly
      2. getattr via SiteReadingTypeProperty metadata annotation
      3. getattr via k.rstrip("_set") / k.rstrip("_unset") for bitwise flag checks
    """
    required: set[str] = set()

    for field_name, field_info in params_cls.model_fields.items():
        # Pattern 2: SiteReadingTypeProperty annotation → getattr(dto, property.name, None)
        for meta in field_info.metadata:
            if isinstance(meta, SiteReadingTypeProperty):
                required.add(meta.name)

        # Pattern 3: bitwise _set / _unset fields → getattr(dto, k.rstrip("_set/_unset"))
        if field_name.endswith("_set"):
            required.add(field_name.rstrip("_set"))
        elif field_name.endswith("_unset"):
            required.add(field_name.rstrip("_unset"))

    return required


def test_site_der_setting_dto_covers_all_params() -> None:
    """Every field that ParamsDERSettingsContents accesses on SiteDERSetting must exist on the DTO."""
    dto_field_names = {f.name for f in fields(dtos.SiteDERSetting)}
    required = _fields_referenced_by_params(ParamsDERSettingsContents) | {"grad_w"}

    missing = required - dto_field_names
    assert not missing, f"SiteDERSetting DTO is missing fields required by ParamsDERSettingsContents: {missing}"


def test_site_der_setting_dto_has_no_extra_fields() -> None:
    """SiteDERSetting DTO should contain only fields required by ParamsDERSettingsContents."""
    dto_field_names = {f.name for f in fields(dtos.SiteDERSetting)}
    required = _fields_referenced_by_params(ParamsDERSettingsContents) | {"grad_w"}

    # Required for comparing settings against capability
    required = required.union(e.multiplier_property for e in DER_VALUE_EQUIVALENTS)

    extra = dto_field_names - required
    assert not extra, f"SiteDERSetting DTO has fields not required by ParamsDERSettingsContents: {extra}"


def test_site_der_rating_dto_covers_all_params() -> None:
    """Every field that ParamsDERCapabilityContents accesses on SiteDERRating must exist on the DTO."""
    dto_field_names = {f.name for f in fields(dtos.SiteDERRating)}
    required = _fields_referenced_by_params(ParamsDERCapabilityContents)

    missing = required - dto_field_names
    assert not missing, f"SiteDERRating DTO is missing fields required by ParamsDERCapabilityContents: {missing}"


def test_site_der_rating_dto_has_no_extra_fields() -> None:
    """SiteDERRating DTO should contain only fields required by ParamsDERCapabilityContents."""
    dto_field_names = {f.name for f in fields(dtos.SiteDERRating)}
    required = _fields_referenced_by_params(ParamsDERCapabilityContents)

    # Required for comparing settings against capability
    required = required.union(e.multiplier_property for e in DER_VALUE_EQUIVALENTS)

    extra = dto_field_names - required
    assert not extra, f"SiteDERRating DTO has fields not required by ParamsDERCapabilityContents: {extra}"


def test_der_dtos_cover_all_equivalents() -> None:
    """Every property used to compare settings against capability must exist on the DTOs."""
    setting_fields = {f.name for f in fields(dtos.SiteDERSetting)}
    rating_fields = {f.name for f in fields(dtos.SiteDERRating)}

    for e in DER_VALUE_EQUIVALENTS:
        assert {e.value_property, e.multiplier_property} <= setting_fields
        assert {e.value_property, e.multiplier_property} <= rating_fields
    for m in DER_MODES_EQUIVALENTS:
        assert m.setting_property in setting_fields
        assert m.rating_property in rating_fields

    # Every value in DERCapability has an equivalent setting - so every field should be covered
    compared = {p for e in DER_VALUE_EQUIVALENTS for p in (e.value_property, e.multiplier_property)}
    compared |= {m.rating_property for m in DER_MODES_EQUIVALENTS}
    assert compared == rating_fields
