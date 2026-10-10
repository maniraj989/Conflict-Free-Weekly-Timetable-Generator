"""Backtracking search with Forward Checking and Minimum Remaining Values (MRV).

Provides constraint-satisfaction scheduling for weekly academic timetables:
- Each theory session is a 1-period unit of work.
- Each laboratory session is a 2-consecutive-period unit of work placed atomically.
- Enforces MRV heuristic (choosing unit with fewest valid candidates).
- Enforces Forward Checking (pruning branches immediately if any remaining unit loses all valid candidates).
- Implements reversible assignments (safely unassigning slots on backtracking).
- Provides configurable search-node limit to protect execution time.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple
from models import Assignment, Faculty, Room, Subject, Timetable
from parser import ParsedData
from validator import can_place_lab_block, can_place_subject


class SearchStatus(str, Enum):
    """Status outcomes for constraint satisfaction search."""

    SOLVED = "SOLVED"
    INFEASIBLE = "INFEASIBLE"
    LIMIT_REACHED = "LIMIT_REACHED"


@dataclass
class SchedulingUnit:
    """A discrete unit of scheduling workload.

    Attributes:
        unit_id: Unique identifier for this unit (e.g., 'X_TH_1', 'X_LAB_1').
        class_name: Target student class cohort.
        subject: The Subject model reference.
        is_lab: True for 2-period lab blocks, False for 1-period theory classes.
        periods: Number of periods occupied (1 for theory, 2 for lab).
        faculty: Name of assigned instructor.
    """

    unit_id: str
    class_name: str
    subject: Subject
    is_lab: bool
    periods: int
    faculty: str


@dataclass
class CandidatePlacement:
    """A candidate slot and venue assignment for a SchedulingUnit."""

    day: str
    start_period: int
    room: Room


@dataclass
class SearchResult:
    """Outcome and diagnostics of the backtracking search."""

    status: SearchStatus
    timetable: Optional[Timetable]
    nodes_explored: int
    max_nodes: int
    message: str
    assignments_made: int


def create_scheduling_units(parsed_data: ParsedData) -> List[SchedulingUnit]:
    """Break down the curriculum requirements of all classes into atomic units of work.

    - Theory subjects: 1 period per unit (periods_per_week units).
    - Laboratory subjects: 2 periods per unit (periods_per_week // 2 units).
    """
    units: List[SchedulingUnit] = []

    for class_name, group in parsed_data.classes.items():
        for subject in group.subjects:
            if subject.is_lab:
                # 2 periods per lab block
                num_blocks = subject.periods_per_week // 2
                for block_idx in range(1, num_blocks + 1):
                    units.append(
                        SchedulingUnit(
                            unit_id=f"{class_name}_{subject.name}_block{block_idx}",
                            class_name=class_name,
                            subject=subject,
                            is_lab=True,
                            periods=2,
                            faculty=subject.faculty,
                        )
                    )
            else:
                # 1 period per theory session
                for period_idx in range(1, subject.periods_per_week + 1):
                    units.append(
                        SchedulingUnit(
                            unit_id=f"{class_name}_{subject.name}_p{period_idx}",
                            class_name=class_name,
                            subject=subject,
                            is_lab=False,
                            periods=1,
                            faculty=subject.faculty,
                        )
                    )

    return units


def find_valid_candidates(
    timetable: Timetable,
    unit: SchedulingUnit,
    parsed_data: ParsedData,
) -> List[CandidatePlacement]:
    """Find all valid candidate placements for a unit in the current timetable state.

    Uses exclusively can_place_subject() and can_place_lab_block() from validator.py.
    """
    candidates: List[CandidatePlacement] = []
    faculty_obj = parsed_data.faculty.get(unit.faculty)

    if unit.is_lab:
        lab_rooms = [r for r in parsed_data.rooms.values() if r.room_type == "L"]
        for day in timetable.days:
            for start_period in range(1, timetable.periods_per_day):
                for room in lab_rooms:
                    is_valid, _ = can_place_lab_block(
                        timetable=timetable,
                        class_name=unit.class_name,
                        day=day,
                        start_period=start_period,
                        subject=unit.subject,
                        room=room,
                        faculty=faculty_obj,
                    )
                    if is_valid:
                        candidates.append(
                            CandidatePlacement(day=day, start_period=start_period, room=room)
                        )
    else:
        theory_rooms = [r for r in parsed_data.rooms.values() if r.room_type == "T"]
        for day in timetable.days:
            for period in range(1, timetable.periods_per_day + 1):
                for room in theory_rooms:
                    is_valid, _ = can_place_subject(
                        timetable=timetable,
                        class_name=unit.class_name,
                        day=day,
                        period=period,
                        subject=unit.subject,
                        room=room,
                        faculty=faculty_obj,
                    )
                    if is_valid:
                        candidates.append(
                            CandidatePlacement(day=day, start_period=period, room=room)
                        )

    return candidates


def apply_assignment(
    timetable: Timetable, unit: SchedulingUnit, placement: CandidatePlacement
) -> None:
    """Tentatively apply a candidate placement to the timetable.

    For labs, atomically occupies both consecutive slots.
    """
    if unit.is_lab:
        timetable.assign(
            class_name=unit.class_name,
            day=placement.day,
            period=placement.start_period,
            assignment=Assignment(
                subject=unit.subject.name,
                faculty=unit.faculty,
                room=placement.room.name,
            ),
        )
        timetable.assign(
            class_name=unit.class_name,
            day=placement.day,
            period=placement.start_period + 1,
            assignment=Assignment(
                subject=unit.subject.name,
                faculty=unit.faculty,
                room=placement.room.name,
            ),
        )
    else:
        timetable.assign(
            class_name=unit.class_name,
            day=placement.day,
            period=placement.start_period,
            assignment=Assignment(
                subject=unit.subject.name,
                faculty=unit.faculty,
                room=placement.room.name,
            ),
        )


def undo_assignment(
    timetable: Timetable, unit: SchedulingUnit, placement: CandidatePlacement
) -> None:
    """Reversibly remove a tentative assignment from the timetable.

    For labs, rolls back both consecutive slots together.
    """
    if unit.class_name in timetable.grid and placement.day in timetable.grid[unit.class_name]:
        timetable.grid[unit.class_name][placement.day][placement.start_period] = None
        if unit.is_lab:
            timetable.grid[unit.class_name][placement.day][placement.start_period + 1] = None


def backtrack_search(
    parsed_data: ParsedData, max_nodes: int = 5000
) -> SearchResult:
    """Find a conflict-free timetable using Backtracking, Forward Checking, and MRV.

    Features:
    - Minimum Remaining Values (MRV): Prioritizes the unscheduled unit with the fewest
      valid placement options to fail-first and prune branches early.
    - Forward Checking: Evaluates remaining unscheduled units after every tentative assignment.
      If any remaining unit has 0 valid options, the branch is rolled back immediately.
    - Reversible assignments: Only removes tentative assignments belonging to the active branch.
    - Search limit: Protects against combinatorial explosions, returning LIMIT_REACHED if exceeded.

    Args:
        parsed_data: Parsed timetable configuration.
        max_nodes: Maximum number of search nodes (variable assignments attempted) allowed.

    Returns:
        SearchResult indicating SOLVED, INFEASIBLE, or LIMIT_REACHED.
    """
    timetable = Timetable(
        days=list(parsed_data.days),
        periods_per_day=parsed_data.periods_per_day,
    )
    for class_name in parsed_data.classes:
        timetable.initialize_class(class_name)

    units = create_scheduling_units(parsed_data)
    nodes_explored = 0
    limit_reached = False

    def solve(remaining_units: List[SchedulingUnit]) -> bool:
        nonlocal nodes_explored, limit_reached

        if not remaining_units:
            return True

        if nodes_explored >= max_nodes:
            limit_reached = True
            return False

        nodes_explored += 1

        # Evaluate candidate placements for all remaining units
        unit_candidates: List[Tuple[SchedulingUnit, List[CandidatePlacement]]] = []
        for unit in remaining_units:
            candidates = find_valid_candidates(timetable, unit, parsed_data)
            # Forward check before picking: if any unit has 0 options, prune immediately
            if not candidates:
                return False
            unit_candidates.append((unit, candidates))

        # MRV Heuristic: Sort by fewest candidates first
        # Tie-breakers: labs first (larger 2-period constraint), then deterministic unit_id
        unit_candidates.sort(
            key=lambda item: (len(item[1]), 0 if item[0].is_lab else 1, item[0].unit_id)
        )
        chosen_unit, candidates = unit_candidates[0]

        other_units = [u for u in remaining_units if u.unit_id != chosen_unit.unit_id]

        for placement in candidates:
            if nodes_explored >= max_nodes:
                limit_reached = True
                return False

            # 1. Tentative assignment
            apply_assignment(timetable, chosen_unit, placement)

            # 2. Forward checking on remaining units
            forward_check_failed = False
            for rem_unit in other_units:
                rem_options = find_valid_candidates(timetable, rem_unit, parsed_data)
                if not rem_options:
                    forward_check_failed = True
                    break

            # 3. Recurse if forward check passed
            if not forward_check_failed:
                if solve(other_units):
                    return True

            # 4. Rollback / Backtrack
            undo_assignment(timetable, chosen_unit, placement)

        return False

    success = solve(units)

    if success:
        return SearchResult(
            status=SearchStatus.SOLVED,
            timetable=timetable,
            nodes_explored=nodes_explored,
            max_nodes=max_nodes,
            message="Optimal conflict-free timetable found successfully.",
            assignments_made=len(units),
        )
    elif limit_reached:
        return SearchResult(
            status=SearchStatus.LIMIT_REACHED,
            timetable=None,
            nodes_explored=nodes_explored,
            max_nodes=max_nodes,
            message=f"Configured search limit reached ({nodes_explored}/{max_nodes} nodes explored) before a complete solution could be found.",
            assignments_made=0,
        )
    else:
        return SearchResult(
            status=SearchStatus.INFEASIBLE,
            timetable=None,
            nodes_explored=nodes_explored,
            max_nodes=max_nodes,
            message="No feasible conflict-free timetable exists for the given constraints and resources.",
            assignments_made=0,
        )
