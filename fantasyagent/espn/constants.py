"""Static ESPN fantasy-football identifiers.

ESPN's fantasy API is undocumented; these mappings are stable in practice but
are the first thing to check if the agent starts behaving strangely.
"""

# Lineup slot ids. The ones a redraft league actually uses are QB..FLEX.
SLOT_QB = 0
SLOT_RB = 2
SLOT_RB_WR = 3
SLOT_WR = 4
SLOT_WR_TE = 5
SLOT_TE = 6
SLOT_OP = 7
SLOT_DST = 16
SLOT_K = 17
SLOT_BENCH = 20
SLOT_IR = 21
SLOT_FLEX = 23

SLOT_NAMES = {
    0: "QB", 1: "TQB", 2: "RB", 3: "RB/WR", 4: "WR", 5: "WR/TE", 6: "TE",
    7: "OP", 8: "DT", 9: "DE", 10: "LB", 11: "DL", 12: "CB", 13: "S",
    14: "DB", 15: "DP", 16: "D/ST", 17: "K", 18: "P", 19: "HC",
    20: "BE", 21: "IR", 23: "FLEX", 24: "ER",
}

#: Slots that hold players who are not scoring this week.
NON_STARTING_SLOTS = frozenset({SLOT_BENCH, SLOT_IR})

POSITION_NAMES = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "D/ST"}

PRO_TEAM_ABBREV = {
    0: "FA", 1: "ATL", 2: "BUF", 3: "CHI", 4: "CIN", 5: "CLE", 6: "DAL",
    7: "DEN", 8: "DET", 9: "GB", 10: "TEN", 11: "IND", 12: "KC", 13: "LV",
    14: "LAR", 15: "MIA", 16: "MIN", 17: "NE", 18: "NO", 19: "NYG",
    20: "NYJ", 21: "PHI", 22: "ARI", 23: "PIT", 24: "LAC", 25: "SF",
    26: "SEA", 27: "TB", 28: "WSH", 29: "CAR", 30: "JAX", 33: "BAL",
    34: "HOU",
}

# Injury designations, worst to best. Anything at or above OUT is unstartable.
INJURY_OUT = frozenset({"OUT", "INJURY_RESERVE", "SUSPENSION", "NON_FOOTBALL_INJURY", "DOUBTFUL"})
INJURY_RISKY = frozenset({"QUESTIONABLE"})

#: Multiplier applied to a projection for each injury designation. DOUBTFUL and
#: worse are handled as hard exclusions, not discounts.
INJURY_DISCOUNT = {"QUESTIONABLE": 0.80}

# Stat-record discriminators inside player["stats"].
STAT_SOURCE_ACTUAL = 0
STAT_SOURCE_PROJECTED = 1
STAT_SPLIT_WEEKLY = 1
