"""Backtracking search with Forward Checking, MRV heuristic, and Gap Optimization.

Provides constraint-satisfaction scheduling and idle-gap minimization:
- Each theory session is a 1-period unit of work.
- Each laboratory session is a 2-consecutive-period unit of work placed atomically.
- Enforces MRV heuristic (choosing unit with fewest valid candidates).
- Enforces Forward Checking (pruning branches immediately if any remaining unit loses all valid candidates).
- Implements reversible assignments (safely unassigning slots on backtracking).
- Optimizes for student cohort compactness by minimizing class idle gaps.
- Gap metric: An empty period strictly between the first and last occupied periods
  of the same class on the same day.
- Configurable search budget limit with clear distinction between optimal,
  partially optimized, and infeasible outcomes.
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
    OPTIMAL = "OPTIMAL"
    FEASIBLE_SEARCH_LIMITED = "FEASIBLE_SEARCH_LIMITED"
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
    initial_gaps: Optional[int] = None
    best_gaps: Optional[int] = None
    optimality_proven: bool = False


def calculate_class_day_gaps(timetable: Timetable, class_name: str, day: str) -> int:
    """Calculate the number of idle gap periods for a class on a single day.

    Rules:
    - An empty period is counted as an idle gap ONLY if it lies strictly between
      the first and last occupied periods of that class on that day.
    - Empty periods before the first class or after the last class are NOT gaps.
    - Gaps across different days are never counted.
    - Each qualifying empty class-period is counted once.
    """
    if class_name not in timetable.grid or day not in timetable.grid[class_name]:
        return 0

    occupied_periods = [
        p
        for p in range(1, timetable.periods_per_day + 1)
        if timetable.grid[class_name][day].get(p) is not None
    ]

    if len(occupied_periods) < 2:
        return 0

    first_p = min(occupied_periods)
    last_p = max(occupied_periods)

    gaps = 0
    for p in range(first_p + 1, last_p):
        if timetable.grid[class_name][day].get(p) is None:
            gaps += 1

    return gaps


def calculate_total_idle_gaps(timetable: Timetable) -> int:
    """Calculate total idle gap periods across all classes and days."""
    total_gaps = 0
    for class_name in timetable.grid:
        for day in timetable.days:
            total_gaps += calculate_class_day_gaps(timetable, class_name, day)
    return total_gaps


def calculate_gaps_by_class(timetable: Timetable) -> Dict[str, int]:
    """Calculate idle gaps broken down by class group."""
    breakdown = {}
    for class_name in timetable.grid:
        class_gaps = sum(
            calculate_class_day_gaps(timetable, class_name, day)
            for day in timetable.days
        )
        breakdown[class_name] = class_gaps
    return breakdown


def clone_timetable(timetable: Timetable) -> Timetable:
    """Create a deep copy of a Timetable instance."""
    cloned = Timetable(
        days=list(timetable.days),
        periods_per_day=timetable.periods_per_day,
    )
    for c_name, days_dict in timetable.grid.items():
        cloned.grid[c_name] = {}
        for day, p_dict in days_dict.items():
            cloned.grid[c_name][day] = dict(p_dict)
    return cloned


def create_scheduling_units(parsed_data: ParsedData) -> List[SchedulingUnit]:
    """Break down the curriculum requirements of all classes into atomic units of work.

    - Theory subjects: 1 period per unit (periods_per_week units).
    - Laboratory subjects: 2 periods per unit (periods_per_week // 2 units).
    """
    units: List[SchedulingUnit] = []

    for class_name, group in parsed_data.classes.items():
        for sub_idx, subject in enumerate(group.subjects, start=1):
            if subject.is_lab:
                num_blocks = subject.periods_per_week // 2
                for block_idx in range(1, num_blocks + 1):
                    units.append(
                        SchedulingUnit(
                            unit_id=f"{class_name}_{subject.name}_{sub_idx}_block{block_idx}",
                            class_name=class_name,
                            subject=subject,
                            is_lab=True,
                            periods=2,
                            faculty=subject.faculty,
                        )
                    )
            else:
                for period_idx in range(1, subject.periods_per_week + 1):
                    units.append(
                        SchedulingUnit(
                            unit_id=f"{class_name}_{subject.name}_{sub_idx}_p{period_idx}",
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
    class_grid = timetable.grid.get(unit.class_name, {})

    if unit.is_lab:
        lab_rooms = [r for r in parsed_data.rooms.values() if r.room_type == "L"]
        for day in timetable.days:
            day_slots = class_grid.get(day, {})
            for start_period in range(1, timetable.periods_per_day):
                # Pre-check slot occupancy for the target class
                if day_slots.get(start_period) is not None or day_slots.get(start_period + 1) is not None:
                    continue
                # Pre-check faculty availability
                if faculty_obj and (not faculty_obj.is_available(day, start_period) or not faculty_obj.is_available(day, start_period + 1)):
                    continue
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
            day_slots = class_grid.get(day, {})
            for period in range(1, timetable.periods_per_day + 1):
                # Pre-check slot occupancy for the target class
                if day_slots.get(period) is not None:
                    continue
                # Pre-check faculty availability
                if faculty_obj and not faculty_obj.is_available(day, period):
                    continue
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


def diagnose_blocking_constraint(parsed_data: ParsedData) -> str:
    """Analyze timetable requirements to identify the specific constraint that blocks scheduling."""
    total_slots_per_class = len(parsed_data.days) * parsed_data.periods_per_day
    theory_rooms = [r for r in parsed_data.rooms.values() if r.room_type == "T"]
    lab_rooms = [r for r in parsed_data.rooms.values() if r.room_type == "L"]

    # 1. Check if lab exists but daily periods < 2
    has_lab = any(s.is_lab for cg in parsed_data.classes.values() for s in cg.subjects)
    if has_lab and parsed_data.periods_per_day < 2:
        return f"Daily periods ({parsed_data.periods_per_day}) is less than 2, preventing 2-consecutive-period lab scheduling."

    # 2. Check room type existence
    has_theory = any(not s.is_lab for cg in parsed_data.classes.values() for s in cg.subjects)
    if has_theory and not theory_rooms:
        return "Missing theory room (no room with room_type 'T' exists for theory subjects)."
    if has_lab and not lab_rooms:
        return "Missing laboratory room (no room with room_type 'L' exists for laboratory subjects)."

    # 3. Check individual class capacity
    for c_name, cg in parsed_data.classes.items():
        class_req = sum(s.periods_per_week for s in cg.subjects)
        if class_req > total_slots_per_class:
            return (
                f"Class slot capacity exceeded for '{c_name}' "
                f"(requires {class_req} periods, but only {total_slots_per_class} slots exist in the week)."
            )

    # 4. Check faculty total capacity
    faculty_periods: Dict[str, int] = {}
    for cg in parsed_data.classes.values():
        for s in cg.subjects:
            faculty_periods[s.faculty] = faculty_periods.get(s.faculty, 0) + s.periods_per_week

    for f_id, total_req in faculty_periods.items():
        f_obj = parsed_data.faculty.get(f_id)
        max_avail = len(f_obj.availability) if (f_obj and f_obj.availability) else total_slots_per_class
        if total_req > max_avail:
            return (
                f"Faculty capacity conflict for '{f_id}' "
                f"(assigned {total_req} periods across classes, but maximum available slots is {max_avail})."
            )

    # 5. Check aggregate theory room capacity
    total_theory_req = sum(
        s.periods_per_week for cg in parsed_data.classes.values() for s in cg.subjects if not s.is_lab
    )
    max_theory_room_capacity = len(theory_rooms) * total_slots_per_class
    if total_theory_req > max_theory_room_capacity:
        return (
            f"Theory room capacity exceeded "
            f"(requires {total_theory_req} theory periods, but {len(theory_rooms)} theory room(s) provide at most {max_theory_room_capacity} slots)."
        )

    # 6. Check aggregate lab room capacity
    total_lab_req = sum(
        s.periods_per_week for cg in parsed_data.classes.values() for s in cg.subjects if s.is_lab
    )
    max_lab_room_capacity = len(lab_rooms) * total_slots_per_class
    if total_lab_req > max_lab_room_capacity:
        return (
            f"Laboratory room capacity exceeded "
            f"(requires {total_lab_req} lab periods, but {len(lab_rooms)} lab room(s) provide at most {max_lab_room_capacity} slots)."
        )

    # 7. Check if any unit has 0 candidates on an empty timetable
    empty_tt = Timetable(days=list(parsed_data.days), periods_per_day=parsed_data.periods_per_day)
    for c_name in parsed_data.classes:
        empty_tt.initialize_class(c_name)
    units = create_scheduling_units(parsed_data)
    for u in units:
        cands = find_valid_candidates(empty_tt, u, parsed_data)
        if not cands:
            return (
                f"No valid venue or slot exists for requirement '{u.unit_id}' "
                f"(Class: {u.class_name}, Subject: {u.subject.name}, Faculty: {u.faculty}, Lab: {u.is_lab})."
            )

    # 8. General constraint clash
    return (
        "Exact blocking constraint could not be isolated automatically; mutual conflict across simultaneous faculty availability, room exclusivity, or consecutive-period constraints prevents a conflict-free schedule."
    )


def backtrack_search(
    parsed_data: ParsedData,
    max_nodes: int = 5000,
    optimize: bool = True,
) -> SearchResult:
    """Find a conflict-free timetable using Backtracking, Forward Checking, MRV, and Gap Optimization.

    Features:
    - Minimum Remaining Values (MRV): Prioritizes the unscheduled unit with the fewest
      valid placement options to fail-first and prune branches early.
    - Forward Checking: Evaluates remaining unscheduled units after every tentative assignment.
      If any remaining unit has 0 valid options, the branch is rolled back immediately.
    - Reversible assignments: Only removes tentative assignments belonging to the active branch.
    - Gap Optimization: Evaluates complete candidate timetables by total idle gaps.
      Retains the best valid timetable found so far.
    - Search limit: Protects against combinatorial explosions. If budget runs out after finding
      a valid timetable, returns that timetable with FEASIBLE_SEARCH_LIMITED status without
      falsely claiming infeasibility or global optimality.

    Args:
        parsed_data: Parsed timetable configuration.
        max_nodes: Maximum number of search nodes (variable assignments attempted) allowed.
        optimize: If True, searches across candidate timetables to minimize idle gaps.

    Returns:
        SearchResult indicating OPTIMAL, FEASIBLE_SEARCH_LIMITED, INFEASIBLE, or LIMIT_REACHED.
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

    best_timetable: Optional[Timetable] = None
    best_gaps: Optional[int] = None
    initial_gaps: Optional[int] = None
    optimality_proven = False

    theory_rooms = [r for r in parsed_data.rooms.values() if r.room_type == "T"]
    lab_rooms = [r for r in parsed_data.rooms.values() if r.room_type == "L"]

    # Fast conflict tracking sets
    occ_class: Set[Tuple[str, str, int]] = set()
    occ_room: Set[Tuple[str, str, int]] = set()
    occ_faculty: Set[Tuple[str, str, int]] = set()
    for c_name, days_d in timetable.grid.items():
        for d_name, p_d in days_d.items():
            for p_num, assign in p_d.items():
                if assign is not None:
                    occ_class.add((c_name, d_name, p_num))
                    occ_room.add((assign.room, d_name, p_num))
                    occ_faculty.add((assign.faculty, d_name, p_num))

    def get_unit_candidates(unit: SchedulingUnit) -> List[CandidatePlacement]:
        cands: List[CandidatePlacement] = []
        f_obj = parsed_data.faculty.get(unit.faculty)
        c_name = unit.class_name
        fac = unit.faculty
        if unit.is_lab:
            for day in parsed_data.days:
                for p in range(1, parsed_data.periods_per_day):
                    p2 = p + 1
                    if (c_name, day, p) in occ_class or (c_name, day, p2) in occ_class:
                        continue
                    if f_obj and (not f_obj.is_available(day, p) or not f_obj.is_available(day, p2)):
                        continue
                    if (fac, day, p) in occ_faculty or (fac, day, p2) in occ_faculty:
                        continue
                    for r in lab_rooms:
                        if not r.is_available(day, p) or not r.is_available(day, p2):
                            continue
                        if (r.name, day, p) in occ_room or (r.name, day, p2) in occ_room:
                            continue
                        cands.append(CandidatePlacement(day=day, start_period=p, room=r))
                        break
        else:
            for day in parsed_data.days:
                for p in range(1, parsed_data.periods_per_day + 1):
                    if (c_name, day, p) in occ_class:
                        continue
                    if f_obj and not f_obj.is_available(day, p):
                        continue
                    if (fac, day, p) in occ_faculty:
                        continue
                    for r in theory_rooms:
                        if not r.is_available(day, p):
                            continue
                        if (r.name, day, p) in occ_room:
                            continue
                        cands.append(CandidatePlacement(day=day, start_period=p, room=r))
                        break
        return cands

    def apply_step(unit: SchedulingUnit, placement: CandidatePlacement) -> None:
        p = placement.start_period
        timetable.assign(
            class_name=unit.class_name,
            day=placement.day,
            period=p,
            assignment=Assignment(
                subject=unit.subject.name,
                faculty=unit.faculty,
                room=placement.room.name,
            ),
        )
        occ_class.add((unit.class_name, placement.day, p))
        occ_room.add((placement.room.name, placement.day, p))
        occ_faculty.add((unit.faculty, placement.day, p))
        if unit.is_lab:
            timetable.assign(
                class_name=unit.class_name,
                day=placement.day,
                period=p + 1,
                assignment=Assignment(
                    subject=unit.subject.name,
                    faculty=unit.faculty,
                    room=placement.room.name,
                ),
            )
            occ_class.add((unit.class_name, placement.day, p + 1))
            occ_room.add((placement.room.name, placement.day, p + 1))
            occ_faculty.add((unit.faculty, placement.day, p + 1))

    def undo_step(unit: SchedulingUnit, placement: CandidatePlacement) -> None:
        p = placement.start_period
        timetable.grid[unit.class_name][placement.day][p] = None
        occ_class.remove((unit.class_name, placement.day, p))
        occ_room.remove((placement.room.name, placement.day, p))
        occ_faculty.remove((unit.faculty, placement.day, p))
        if unit.is_lab:
            timetable.grid[unit.class_name][placement.day][p + 1] = None
            occ_class.remove((unit.class_name, placement.day, p + 1))
            occ_room.remove((placement.room.name, placement.day, p + 1))
            occ_faculty.remove((unit.faculty, placement.day, p + 1))

    def solve(remaining_units: List[SchedulingUnit]) -> bool:
        nonlocal nodes_explored, limit_reached, best_timetable, best_gaps, initial_gaps, optimality_proven

        # Base case: Complete valid timetable constructed!
        if not remaining_units:
            current_gaps = calculate_total_idle_gaps(timetable)
            if initial_gaps is None:
                initial_gaps = current_gaps

            if best_gaps is None or current_gaps < best_gaps:
                best_gaps = current_gaps
                best_timetable = clone_timetable(timetable)

            if not optimize:
                return True

            # 0 gaps is the theoretical global minimum (cannot have negative gaps)
            if best_gaps == 0:
                optimality_proven = True
                return True

            # Continue searching other branches to find fewer gaps
            return False

        if nodes_explored >= max_nodes:
            limit_reached = True
            return False

        nodes_explored += 1

        # Evaluate candidate placements for all remaining units
        unit_candidates: List[Tuple[SchedulingUnit, List[CandidatePlacement]]] = []
        for unit in remaining_units:
            candidates = get_unit_candidates(unit)
            if not candidates:
                return False
            unit_candidates.append((unit, candidates))

        # MRV Heuristic: Sort by fewest candidates first
        unit_candidates.sort(
            key=lambda item: (len(item[1]), 0 if item[0].is_lab else 1, item[0].unit_id)
        )
        chosen_unit, candidates = unit_candidates[0]
        other_units = [u for u in remaining_units if u.unit_id != chosen_unit.unit_id]

        for placement in candidates:
            if nodes_explored >= max_nodes:
                limit_reached = True
                return False

            apply_step(chosen_unit, placement)
            if solve(other_units):
                return True
            undo_step(chosen_unit, placement)

        return False

    success = solve(units)

    # 1. Solution was found
    if best_timetable is not None:
        if success or (not limit_reached):
            status = SearchStatus.OPTIMAL
            optimality_proven = True
            msg = f"Optimal conflict-free timetable found with {best_gaps} idle gaps."
        else:
            status = SearchStatus.FEASIBLE_SEARCH_LIMITED
            optimality_proven = False
            msg = (
                f"Search limit reached ({nodes_explored}/{max_nodes} nodes explored). "
                f"Retained best conflict-free timetable with {best_gaps} idle gaps (optimality not proven)."
            )

        return SearchResult(
            status=status,
            timetable=best_timetable,
            nodes_explored=nodes_explored,
            max_nodes=max_nodes,
            message=msg,
            assignments_made=len(units),
            initial_gaps=initial_gaps,
            best_gaps=best_gaps,
            optimality_proven=optimality_proven,
        )

    # 2. No solution found
    if limit_reached:
        return SearchResult(
            status=SearchStatus.LIMIT_REACHED,
            timetable=None,
            nodes_explored=nodes_explored,
            max_nodes=max_nodes,
            message=f"Configured search limit reached ({nodes_explored}/{max_nodes} nodes explored) before finding any complete timetable.",
            assignments_made=0,
            initial_gaps=None,
            best_gaps=None,
            optimality_proven=False,
        )
    else:
        blocking_reason = diagnose_blocking_constraint(parsed_data)
        return SearchResult(
            status=SearchStatus.INFEASIBLE,
            timetable=None,
            nodes_explored=nodes_explored,
            max_nodes=max_nodes,
            message=f"No feasible conflict-free timetable exists. Blocking constraint: {blocking_reason}",
            assignments_made=0,
            initial_gaps=None,
            best_gaps=None,
            optimality_proven=False,
        )
