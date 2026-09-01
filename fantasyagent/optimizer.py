"""Pick the highest-scoring legal starting lineup.

Slotting is an assignment problem, not a greedy sort: a WR who is the best
option in FLEX may be needed at WR, and filling slots one at a time in some
arbitrary order can leave points on the bench. We solve it exactly with the
Hungarian algorithm over (starting slot, player) pairs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from .espn.constants import SLOT_BENCH, SLOT_NAMES
from .espn.models import Player, Roster

#: Cost used for an ineligible pairing; large enough to never be chosen, small
#: enough to stay well inside float precision.
_INELIGIBLE = 1e6

#: Nudge favouring a player already in the slot, so we do not churn the lineup
#: to break an exact tie.
_INCUMBENT_BONUS = 1e-4


@dataclass
class Move:
    player: Player
    from_slot: int
    to_slot: int

    @property
    def is_promotion(self) -> bool:
        return self.from_slot == SLOT_BENCH

    def describe(self) -> str:
        return (
            f"{SLOT_NAMES.get(self.from_slot, self.from_slot)} -> "
            f"{SLOT_NAMES.get(self.to_slot, self.to_slot)}: {self.player.name}"
        )

    def as_payload(self) -> Dict[str, int]:
        return {
            "player_id": self.player.player_id,
            "from_slot": self.from_slot,
            "to_slot": self.to_slot,
        }


@dataclass
class Lineup:
    assignments: Dict[int, int] = field(default_factory=dict)  # player_id -> slot
    moves: List[Move] = field(default_factory=list)
    locked: List[Player] = field(default_factory=list)
    unfilled_slots: List[int] = field(default_factory=list)
    projected_total: float = 0.0

    @property
    def has_changes(self) -> bool:
        return bool(self.moves)


def _hungarian(cost: Sequence[Sequence[float]]) -> List[int]:
    """Min-cost assignment. Returns ``row -> column`` (``-1`` if unassigned).

    Requires ``len(cost) <= len(cost[0])``.
    """
    n, m = len(cost), len(cost[0])
    inf = float("inf")
    u = [0.0] * (n + 1)
    v = [0.0] * (m + 1)
    match = [0] * (m + 1)  # column -> row (1-indexed, 0 = free)
    way = [0] * (m + 1)

    for i in range(1, n + 1):
        match[0] = i
        j0 = 0
        minv = [inf] * (m + 1)
        used = [False] * (m + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = match[j0], inf, -1
            for j in range(1, m + 1):
                if used[j]:
                    continue
                cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                if cur < minv[j]:
                    minv[j], way[j] = cur, j0
                if minv[j] < delta:
                    delta, j1 = minv[j], j
            for j in range(m + 1):
                if used[j]:
                    u[match[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if match[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            match[j0] = match[j1]
            j0 = j1

    result = [-1] * n
    for j in range(1, m + 1):
        if match[j] > 0:
            result[match[j] - 1] = j - 1
    return result


def _expand_slots(slot_counts: Dict[int, int]) -> List[int]:
    slots: List[int] = []
    for slot_id, count in sorted(slot_counts.items()):
        slots.extend([slot_id] * count)
    return slots


def optimize(roster: Roster, slot_counts: Dict[int, int]) -> Lineup:
    """Return the best legal lineup and the moves needed to reach it."""
    lineup = Lineup()
    slots = _expand_slots(slot_counts)

    # Players whose game has kicked off are frozen by ESPN. Honour that first:
    # they keep their slot, and a starting slot they occupy is off the board.
    candidates: List[Player] = []
    for player in roster.players:
        if player.game_started:
            lineup.locked.append(player)
            lineup.assignments[player.player_id] = player.current_slot
            if player.current_slot in slots:
                slots.remove(player.current_slot)
        else:
            candidates.append(player)

    if not slots:
        # Every starting slot is already locked; nothing left to decide.
        for player in candidates:
            lineup.assignments[player.player_id] = SLOT_BENCH
        lineup.projected_total = sum(p.score for p in lineup.locked if p.is_starting)
        return lineup

    # Pad with phantom players so the matrix is never wider than it is tall;
    # a slot matched to a phantom simply stays empty.
    padding = max(0, len(slots) - len(candidates))
    width = len(candidates) + padding

    cost: List[List[float]] = []
    for slot_id in slots:
        row: List[float] = []
        for player in candidates:
            if slot_id in player.eligible_slots:
                value = player.score
                if player.current_slot == slot_id:
                    value += _INCUMBENT_BONUS
                row.append(-value)
            else:
                row.append(_INELIGIBLE)
        row.extend([_INELIGIBLE] * padding)
        cost.append(row)

    assignment = _hungarian(cost)

    for slot_index, player_index in enumerate(assignment):
        slot_id = slots[slot_index]
        if player_index < 0 or player_index >= len(candidates):
            lineup.unfilled_slots.append(slot_id)
            continue
        if cost[slot_index][player_index] >= _INELIGIBLE:
            lineup.unfilled_slots.append(slot_id)
            continue
        lineup.assignments[candidates[player_index].player_id] = slot_id

    for player in candidates:
        lineup.assignments.setdefault(player.player_id, SLOT_BENCH)

    for player in roster.players:
        target = lineup.assignments[player.player_id]
        if target != player.current_slot:
            lineup.moves.append(Move(player, player.current_slot, target))
        if target != SLOT_BENCH:
            lineup.projected_total += player.score

    lineup.moves.sort(key=lambda m: (m.to_slot == SLOT_BENCH, m.to_slot))
    return lineup


def starters(roster: Roster, lineup: Lineup) -> List[Player]:
    out = [p for p in roster.players if lineup.assignments.get(p.player_id) != SLOT_BENCH]
    return sorted(out, key=lambda p: lineup.assignments[p.player_id])


def bench(roster: Roster, lineup: Lineup) -> List[Player]:
    out = [p for p in roster.players if lineup.assignments.get(p.player_id) == SLOT_BENCH]
    return sorted(out, key=lambda p: -p.score)
