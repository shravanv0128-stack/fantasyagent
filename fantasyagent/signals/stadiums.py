"""Static NFL stadium facts: location and roof type.

Team relocations and stadium changes are rare enough that a hardcoded table
is the right call — no external dependency for something that changes maybe
once every few years. Keyed by the same pro team id used throughout ESPN's
API (see espn/constants.py).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Stadium:
    lat: float
    lon: float
    #: "outdoor" always exposed to weather; "dome" never is;
    #: "retractable" depends on whether the roof happens to be open.
    roof: str


# Coordinates are the stadium's, not the team's home city generally, so a
# forecast lookup lands on the right microclimate.
STADIUMS = {
    1: Stadium(33.7554, -84.4008, "retractable"),   # ATL — Mercedes-Benz
    2: Stadium(42.7738, -78.7870, "outdoor"),        # BUF — Highmark
    3: Stadium(41.8623, -87.6167, "outdoor"),        # CHI — Soldier Field
    4: Stadium(39.0954, -84.5160, "outdoor"),        # CIN — Paycor
    5: Stadium(41.5061, -81.6995, "outdoor"),        # CLE — Huntington Bank
    6: Stadium(32.7473, -97.0945, "retractable"),    # DAL — AT&T
    7: Stadium(39.7439, -105.0201, "outdoor"),       # DEN — Empower Field
    8: Stadium(42.3400, -83.0456, "dome"),           # DET — Ford Field
    9: Stadium(44.5013, -88.0622, "outdoor"),        # GB — Lambeau
    10: Stadium(36.1665, -86.7713, "outdoor"),       # TEN — Nissan
    11: Stadium(39.7601, -86.1639, "dome"),          # IND — Lucas Oil
    12: Stadium(39.0489, -94.4839, "outdoor"),       # KC — Arrowhead
    13: Stadium(36.0909, -115.1833, "dome"),         # LV — Allegiant
    14: Stadium(33.9535, -118.3392, "outdoor"),      # LAR — SoFi
    15: Stadium(25.9580, -80.2389, "outdoor"),       # MIA — Hard Rock
    16: Stadium(44.9735, -93.2575, "dome"),          # MIN — US Bank
    17: Stadium(42.0909, -71.2643, "outdoor"),       # NE — Gillette
    18: Stadium(29.9511, -90.0812, "dome"),          # NO — Caesars Superdome
    19: Stadium(40.8135, -74.0745, "outdoor"),       # NYG — MetLife
    20: Stadium(40.8135, -74.0745, "outdoor"),       # NYJ — MetLife
    21: Stadium(39.9008, -75.1675, "outdoor"),       # PHI — Lincoln Financial
    22: Stadium(33.5276, -112.2626, "retractable"),  # ARI — State Farm
    23: Stadium(40.4468, -80.0158, "outdoor"),       # PIT — Acrisure
    24: Stadium(33.8643, -118.2611, "outdoor"),      # LAC — SoFi
    25: Stadium(37.7133, -122.3861, "outdoor"),      # SF — Levi's
    26: Stadium(47.5952, -122.3316, "outdoor"),      # SEA — Lumen
    27: Stadium(27.9759, -82.5033, "outdoor"),       # TB — Raymond James
    28: Stadium(38.9078, -76.8645, "outdoor"),       # WSH — Northwest / Commanders
    29: Stadium(35.2258, -80.8528, "outdoor"),       # CAR — Bank of America
    30: Stadium(30.3239, -81.6373, "outdoor"),       # JAX — EverBank
    33: Stadium(39.2780, -76.6227, "outdoor"),       # BAL — M&T Bank
    34: Stadium(29.6847, -95.4107, "retractable"),   # HOU — NRG
}
