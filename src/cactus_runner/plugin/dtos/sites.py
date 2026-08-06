from dataclasses import dataclass

__all__ = ["Site"]


@dataclass(slots=True, frozen=True)
class Site:
    site_id: str
    nmi: str | None
    lfdi: str
    sfdi: int
    device_category: int
