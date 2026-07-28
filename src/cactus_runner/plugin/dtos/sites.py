from dataclasses import dataclass

__all__ = ["Site"]


@dataclass(slots=True)
class Site:
    site_id: str
    nmi: str | None
    lfdi: str
    sfdi: int
    aggregator_id: int | None
    device_category: int
    timezone_id: str | None
