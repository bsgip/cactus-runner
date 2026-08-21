from dataclasses import dataclass

__all__ = ["SiteGroup"]

@dataclass(frozen=True, slots=True)
class SiteGroup:
    site_group_id: str
    total_sites: int
