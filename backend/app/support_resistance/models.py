from typing import Literal

from pydantic import BaseModel

SRKind = Literal["SUPPORT", "RESISTANCE"]   # AUD-G1.6: the zone side, for the builders' own annotations


class SRZone(BaseModel):
    kind: Literal["SUPPORT", "RESISTANCE"]
    lower: float
    upper: float
    mid: float
    strength_score: int
    touches: int = 0
    volume_confirmation: bool = False
    rejection_count: int = 0
    timeframe: str
    source: str
