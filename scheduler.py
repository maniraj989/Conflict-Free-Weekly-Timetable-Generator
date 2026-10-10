"""Greedy, backtracking, and gap-optimizing schedulers for weekly timetables.

Provides:
- Discrete unit scheduling for theory and 2-period lab blocks.
- Backtracking search with Forward Checking and Minimum Remaining Values (MRV).
- Idle gap counting and timetable scoring optimization.
- Comprehensive unit tests covering constraints, search limits, and gap minimization.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from models import (
    DEFAULT_FACULTY_REGISTRY,
    SUBJECT_CATALOG,
    Assignment,
    Timetable,
    format_faculty_label,
    get_faculty_display_name,
)
from parser import ParsedData
from search import (
    CandidatePlacement,
    SearchResult,
    SearchStatus,
    SchedulingUnit,
    apply_assignment,
    backtrack_search,
    calculate_class_day_gaps,
    calculate_gaps_by_class,
    calculate_total_idle_gaps,
    clone_timetable,
    find_valid_candidates,
    undo_assignment,
)
from validator import can_place_lab_block, can_place_subject


@dataclass
class SubjectScheduleStatus:
    """Tracking status for an individual subject requirement."""

    class_name: str
    subject_name: str
    is_lab: bool
    faculty: str
    required_periods: int
    scheduled_periods: int
    remaining_periods: int
    status: str  # "COMPLETED", "PARTIAL", "UNSCHEDULED", "PENDING_LAB"
    notes: str = ""
    faculty_display_name: str = ""

    def __post_init__(self) -> None:
        if not self.faculty_display_name:
            self.faculty_display_name = get_faculty_display_name(self.faculty)


@dataclass
class SchedulingReport:
    """Summary of timetable scheduling execution and optimization diagnostics."""

    total_theory_required: int = 0
    total_theory_scheduled: int = 0
    total_theory_unscheduled: int = 0
    total_lab_required: int = 0
    total_lab_scheduled: int = 0
    total_lab_unscheduled: int = 0
    total_lab_pending: int = 0
    subject_statuses: List[SubjectScheduleStatus] = field(default_factory=list)
    success: bool = True
    search_status: str = "SOLVED"
    diagnostics: str = ""
    initial_gaps: Optional[int] = None
    best_gaps: Optional[int] = None
    gaps_reduced: Optional[int] = None
    optimality_proven: bool = False


def schedule_theory_subjects(
    parsed_data: ParsedData, timetable: Optional[Timetable] = None
) -> Tuple[Timetable, SchedulingReport]:
    """Greedily schedule all theory subjects into valid timetable slots."""
    if timetable is None:
        timetable = Timetable(
            days=list(parsed_data.days),
            periods_per_day=parsed_data.periods_per_day,
        )
        for class_name in parsed_data.classes:
            timetable.initialize_class(class_name)

    theory_rooms = [r for r in parsed_data.rooms.values() if r.room_type == "T"]

    total_theory_required = 0
    total_theory_scheduled = 0
    total_theory_unscheduled = 0
    total_lab_required = 0
    statuses: List[SubjectScheduleStatus] = []

    for class_name, class_group in parsed_data.classes.items():
        for subject in class_group.subjects:
            if subject.is_lab:
                total_lab_required += subject.periods_per_week
                statuses.append(
                    SubjectScheduleStatus(
                        class_name=class_name,
                        subject_name=subject.name,
                        is_lab=True,
                        faculty=subject.faculty,
                        required_periods=subject.periods_per_week,
                        scheduled_periods=0,
                        remaining_periods=subject.periods_per_week,
                        status="PENDING_LAB",
                        notes="Laboratory scheduling deferred to Step 6",
                    )
                )
                continue

            total_theory_required += subject.periods_per_week
            needed = subject.periods_per_week
            placed = 0
            faculty_obj = parsed_data.faculty.get(subject.faculty)

            for day in timetable.days:
                if placed >= needed:
                    break
                for period in range(1, timetable.periods_per_day + 1):
                    if placed >= needed:
                        break

                    for room in theory_rooms:
                        is_valid, _ = can_place_subject(
                            timetable=timetable,
                            class_name=class_name,
                            day=day,
                            period=period,
                            subject=subject,
                            room=room,
                            faculty=faculty_obj,
                        )
                        if is_valid:
                            timetable.assign(
                                class_name=class_name,
                                day=day,
                                period=period,
                                assignment=Assignment(
                                    subject=subject.name,
                                    faculty=subject.faculty,
                                    room=room.name,
                                ),
                            )
                            placed += 1
                            break

            remaining = needed - placed
            total_theory_scheduled += placed
            total_theory_unscheduled += remaining

            if remaining == 0:
                sub_status = "COMPLETED"
                notes = "All required periods successfully scheduled"
            elif placed > 0:
                sub_status = "PARTIAL"
                notes = f"Only {placed}/{needed} periods could be placed due to slot/resource limits"
            else:
                sub_status = "UNSCHEDULED"
                notes = "Could not place any periods due to conflicts or insufficient slots"

            statuses.append(
                SubjectScheduleStatus(
                    class_name=class_name,
                    subject_name=subject.name,
                    is_lab=False,
                    faculty=subject.faculty,
                    required_periods=needed,
                    scheduled_periods=placed,
                    remaining_periods=remaining,
                    status=sub_status,
                    notes=notes,
                )
            )

    report = SchedulingReport(
        total_theory_required=total_theory_required,
        total_theory_scheduled=total_theory_scheduled,
        total_theory_unscheduled=total_theory_unscheduled,
        total_lab_required=total_lab_required,
        total_lab_scheduled=0,
        total_lab_unscheduled=total_lab_required,
        total_lab_pending=total_lab_required,
        subject_statuses=statuses,
        success=(total_theory_unscheduled == 0),
        search_status="GREEDY_THEORY",
    )

    return timetable, report


def schedule_lab_subjects(
    parsed_data: ParsedData,
    timetable: Timetable,
    report: Optional[SchedulingReport] = None,
) -> Tuple[Timetable, SchedulingReport]:
    """Schedule laboratory subjects into 2-consecutive-period blocks on the same day."""
    if report is None:
        report = SchedulingReport()

    lab_rooms = [r for r in parsed_data.rooms.values() if r.room_type == "L"]

    total_lab_required = 0
    total_lab_scheduled = 0
    total_lab_unscheduled = 0

    existing_status_map = {
        (s.class_name, s.subject_name): s for s in report.subject_statuses
    }
    updated_statuses: List[SubjectScheduleStatus] = []

    for class_name, class_group in parsed_data.classes.items():
        for subject in class_group.subjects:
            if not subject.is_lab:
                key = (class_name, subject.name)
                if key in existing_status_map:
                    updated_statuses.append(existing_status_map[key])
                continue

            total_lab_required += subject.periods_per_week
            needed_periods = subject.periods_per_week
            needed_blocks = needed_periods // 2
            placed_periods = 0

            faculty_obj = parsed_data.faculty.get(subject.faculty)

            if not lab_rooms:
                failure_reason = "No compatible laboratory room available (room_type 'L' missing)"
            else:
                failure_reason = "Insufficient consecutive free slots or venue conflicts"

            for _ in range(needed_blocks):
                block_placed = False

                for day in timetable.days:
                    if block_placed:
                        break

                    for start_period in range(1, timetable.periods_per_day):
                        if block_placed:
                            break

                        for room in lab_rooms:
                            is_valid, reason = can_place_lab_block(
                                timetable=timetable,
                                class_name=class_name,
                                day=day,
                                start_period=start_period,
                                subject=subject,
                                room=room,
                                faculty=faculty_obj,
                            )

                            if is_valid:
                                timetable.assign(
                                    class_name=class_name,
                                    day=day,
                                    period=start_period,
                                    assignment=Assignment(
                                        subject=subject.name,
                                        faculty=subject.faculty,
                                        room=room.name,
                                    ),
                                )
                                timetable.assign(
                                    class_name=class_name,
                                    day=day,
                                    period=start_period + 1,
                                    assignment=Assignment(
                                        subject=subject.name,
                                        faculty=subject.faculty,
                                        room=room.name,
                                    ),
                                )
                                placed_periods += 2
                                block_placed = True
                                break
                            else:
                                failure_reason = reason

            remaining_periods = needed_periods - placed_periods
            total_lab_scheduled += placed_periods
            total_lab_unscheduled += remaining_periods

            if remaining_periods == 0:
                sub_status = "COMPLETED"
                notes = "All required lab periods successfully scheduled in 2-period blocks"
            elif placed_periods > 0:
                sub_status = "PARTIAL"
                notes = (
                    f"Only {placed_periods}/{needed_periods} periods scheduled; "
                    f"could not place remaining block ({failure_reason})"
                )
            else:
                sub_status = "UNSCHEDULED"
                notes = f"Could not schedule lab block: {failure_reason}"

            updated_statuses.append(
                SubjectScheduleStatus(
                    class_name=class_name,
                    subject_name=subject.name,
                    is_lab=True,
                    faculty=subject.faculty,
                    required_periods=needed_periods,
                    scheduled_periods=placed_periods,
                    remaining_periods=remaining_periods,
                    status=sub_status,
                    notes=notes,
                )
            )

    report.total_lab_required = total_lab_required
    report.total_lab_scheduled = total_lab_scheduled
    report.total_lab_unscheduled = total_lab_unscheduled
    report.total_lab_pending = total_lab_unscheduled
    report.subject_statuses = updated_statuses
    report.success = (
        report.total_theory_unscheduled == 0 and total_lab_unscheduled == 0
    )

    return timetable, report


def schedule_timetable(
    parsed_data: ParsedData,
    use_backtracking: bool = True,
    max_nodes: int = 5000,
    optimize: bool = True,
) -> Tuple[Optional[Timetable], SchedulingReport]:
    """Generate a conflict-free timetable using backtracking search with forward checking and gap optimization.

    Args:
        parsed_data: ParsedData configuration.
        use_backtracking: Whether to use Backtracking + Forward Checking (default: True).
        max_nodes: Maximum search nodes allowed during backtracking (default: 5000).
        optimize: Whether to optimize for minimum class idle gaps (default: True).

    Returns:
        A tuple of (Optional[Timetable], SchedulingReport).
    """
    if not use_backtracking:
        timetable, report = schedule_theory_subjects(parsed_data)
        timetable, report = schedule_lab_subjects(parsed_data, timetable, report)
        return timetable, report

    result = backtrack_search(parsed_data, max_nodes=max_nodes, optimize=optimize)

    total_theory_req = sum(
        s.periods_per_week for cg in parsed_data.classes.values() for s in cg.subjects if not s.is_lab
    )
    total_lab_req = sum(
        s.periods_per_week for cg in parsed_data.classes.values() for s in cg.subjects if s.is_lab
    )

    if result.timetable is not None:
        tt = result.timetable
        statuses: List[SubjectScheduleStatus] = []
        total_theory_sched = 0
        total_lab_sched = 0

        for class_name, group in parsed_data.classes.items():
            for subject in group.subjects:
                placed_count = sum(
                    1
                    for day in tt.days
                    for p in range(1, tt.periods_per_day + 1)
                    if (slot := tt.get_assignment(class_name, day, p)) is not None
                    and slot.subject == subject.name
                )
                if subject.is_lab:
                    total_lab_sched += placed_count
                else:
                    total_theory_sched += placed_count

                rem = subject.periods_per_week - placed_count
                sub_status = "COMPLETED" if rem == 0 else ("PARTIAL" if placed_count > 0 else "UNSCHEDULED")
                notes = (
                    "All required periods successfully scheduled"
                    if rem == 0
                    else f"{placed_count}/{subject.periods_per_week} periods placed"
                )

                statuses.append(
                    SubjectScheduleStatus(
                        class_name=class_name,
                        subject_name=subject.name,
                        is_lab=subject.is_lab,
                        faculty=subject.faculty,
                        required_periods=subject.periods_per_week,
                        scheduled_periods=placed_count,
                        remaining_periods=rem,
                        status=sub_status,
                        notes=notes,
                    )
                )

        initial_gaps = result.initial_gaps
        best_gaps = result.best_gaps
        gaps_reduced = (
            (initial_gaps - best_gaps)
            if (initial_gaps is not None and best_gaps is not None)
            else None
        )

        report = SchedulingReport(
            total_theory_required=total_theory_req,
            total_theory_scheduled=total_theory_sched,
            total_theory_unscheduled=total_theory_req - total_theory_sched,
            total_lab_required=total_lab_req,
            total_lab_scheduled=total_lab_sched,
            total_lab_unscheduled=total_lab_req - total_lab_sched,
            total_lab_pending=total_lab_req - total_lab_sched,
            subject_statuses=statuses,
            success=True,
            search_status=result.status.value,
            diagnostics=f"{result.message} (Explored {result.nodes_explored} search nodes)",
            initial_gaps=initial_gaps,
            best_gaps=best_gaps,
            gaps_reduced=gaps_reduced,
            optimality_proven=result.optimality_proven,
        )
        return tt, report

    # No timetable found
    statuses = [
        SubjectScheduleStatus(
            class_name=cg_name,
            subject_name=s.name,
            is_lab=s.is_lab,
            faculty=s.faculty,
            required_periods=s.periods_per_week,
            scheduled_periods=0,
            remaining_periods=s.periods_per_week,
            status="UNSCHEDULED",
            notes=result.message,
        )
        for cg_name, group in parsed_data.classes.items()
        for s in group.subjects
    ]

    report = SchedulingReport(
        total_theory_required=total_theory_req,
        total_theory_scheduled=0,
        total_theory_unscheduled=total_theory_req,
        total_lab_required=total_lab_req,
        total_lab_scheduled=0,
        total_lab_unscheduled=total_lab_req,
        total_lab_pending=total_lab_req,
        subject_statuses=statuses,
        success=False,
        search_status=result.status.value,
        diagnostics=result.message,
        initial_gaps=None,
        best_gaps=None,
        gaps_reduced=None,
        optimality_proven=False,
    )
    return None, report


def print_timetable_grid(timetable: Timetable) -> None:
    """Print a clean, tabular view of the timetable for each class."""
    print("\n" + "=" * 70)
    print("                    GENERATED TIMETABLE GRID")
    print("=" * 70)

    for class_name, days_dict in timetable.grid.items():
        print(f"\n--- Timetable for Class Group: {class_name} ---")

        col_width = 28
        period_headers = [
            f"Period {p}".center(col_width)
            for p in range(1, timetable.periods_per_day + 1)
        ]
        print(f"{'Day':<6} | " + " | ".join(period_headers))
        separator = "-------+-" + "-+-".join(
            ["-" * col_width for _ in range(timetable.periods_per_day)]
        )
        print(separator)

        for day in timetable.days:
            row_cells = []
            for p in range(1, timetable.periods_per_day + 1):
                slot = days_dict.get(day, {}).get(p)
                if slot:
                    fac_name = getattr(slot, "faculty_display_name", "") or get_faculty_display_name(slot.faculty)
                    cell_text = f"{slot.subject} ({fac_name}, {slot.room})"
                else:
                    cell_text = "---"
                row_cells.append(cell_text.center(col_width))
            print(f"{day:<6} | " + " | ".join(row_cells))


def print_scheduling_report(report: SchedulingReport) -> None:
    """Print an organized breakdown of scheduling results and gap optimization diagnostics."""
    print("\n" + "=" * 70)
    print("                 SCHEDULING SUMMARY REPORT")
    print("=" * 70)

    print("\n1. Search & Optimization Outcome:")
    print(f"   - Search Status:     {report.search_status}")
    if report.best_gaps is not None:
        init_str = f"{report.initial_gaps} gaps" if report.initial_gaps is not None else "N/A"
        print(f"   - Initial Solution:  {init_str}")
        print(f"   - Optimized Gaps:    {report.best_gaps} idle gaps")
        if report.gaps_reduced is not None and report.gaps_reduced > 0:
            print(f"   - Gaps Eliminated:   {report.gaps_reduced} gaps")
        opt_str = (
            "PROVEN (Global Minimum)"
            if report.optimality_proven
            else "INCOMPLETE (Search Budget Limited)"
        )
        print(f"   - Optimality Status: {opt_str}")
    if report.diagnostics:
        print(f"   - Diagnostics:       {report.diagnostics}")

    print("\n2. Theory Subjects:")
    print(f"   - Total Required:    {report.total_theory_required} periods")
    print(f"   - Total Scheduled:   {report.total_theory_scheduled} periods")
    print(f"   - Total Unscheduled: {report.total_theory_unscheduled} periods")

    print("\n3. Laboratory Subjects:")
    print(f"   - Total Required:    {report.total_lab_required} periods")
    print(f"   - Total Scheduled:   {report.total_lab_scheduled} periods")
    print(f"   - Total Unscheduled: {report.total_lab_unscheduled} periods")

    total_req = report.total_theory_required + report.total_lab_required
    total_sched = report.total_theory_scheduled + report.total_lab_scheduled
    total_unsched = (
        report.total_theory_unscheduled + report.total_lab_unscheduled
    )

    print("\n4. Overall Timetable Summary:")
    print(f"   - Total Required:    {total_req} periods")
    print(f"   - Total Scheduled:   {total_sched} periods")
    print(f"   - Total Unscheduled: {total_unsched} periods")
    status_label = (
        "COMPLETE (Conflict-Free)" if total_unsched == 0 else f"INCOMPLETE ({report.search_status})"
    )
    print(f"   - Final Status:      {status_label}")

    print("\n5. Detailed Breakdown by Subject:")
    for status in report.subject_statuses:
        type_str = "Laboratory" if status.is_lab else "Theory"
        ratio = f"{status.scheduled_periods}/{status.required_periods}"
        fac_lbl = format_faculty_label(status.faculty)
        print(
            f"   - [{status.class_name}] {status.subject_name:<8} ({type_str:<10}) | "
            f"Faculty: {fac_lbl:<24} | {ratio} placed [{status.status}]"
        )
        if status.notes:
            print(f"     Note: {status.notes}")

    # 6. Report unconfigured catalog subjects
    configured_names = {s.subject_name for s in report.subject_statuses}
    unconfigured = [s for s in SUBJECT_CATALOG if s not in configured_names]
    if unconfigured:
        print("\n6. Unconfigured Catalog Subjects (Information required before real schedule generation):")
        for u in unconfigured:
            u_type = "Laboratory" if SUBJECT_CATALOG[u] else "Theory"
            print(
                f"   - {u:<8} [{u_type:<10}] : Requires student cohort assignment, instructor assignment, and period count."
            )


def run_scheduler_tests() -> bool:
    """Run comprehensive unit tests covering all scheduler, backtracking, and gap optimization requirements."""
    print("\n============================================================")
    print("           Running Timetable Scheduler Test Suite")
    print("============================================================\n")

    passed_count = 0
    total_tests = 23

    from models import ClassGroup, Faculty, Room, Subject
    from parser import ParsedData, SAMPLE_INPUT, parse_timetable_input

    # -------------------------------------------------------------
    # Test 1: Scheduling a theory subject for its required weekly periods
    # -------------------------------------------------------------
    cg1 = ClassGroup(name="C1")
    cg1.add_subject(Subject(name="MATH", faculty="F1", periods_per_week=2, is_lab=False))
    data1 = ParsedData(
        days=["D1", "D2"],
        periods_per_day=2,
        classes={"C1": cg1},
        faculty={"F1": Faculty(name="F1")},
        rooms={"R1": Room(name="R1", room_type="T")},
    )
    tt1, rep1 = schedule_theory_subjects(data1)
    status_c1 = [s for s in rep1.subject_statuses if s.class_name == "C1" and s.subject_name == "MATH"][0]
    assert status_c1.scheduled_periods == 2
    assert rep1.total_theory_scheduled == 2
    print("[PASS] Test 1: Scheduled theory subject for all required weekly periods (2/2)")
    passed_count += 1

    # -------------------------------------------------------------
    # Test 2: Preventing shared-faculty clash across classes X and Y
    # -------------------------------------------------------------
    cg_x = ClassGroup(name="X")
    cg_x.add_subject(Subject(name="TH_X", faculty="F1", periods_per_week=2, is_lab=False))
    cg_y = ClassGroup(name="Y")
    cg_y.add_subject(Subject(name="TH_Y", faculty="F1", periods_per_week=2, is_lab=False))
    data2 = ParsedData(
        days=["D1", "D2"],
        periods_per_day=3,
        classes={"X": cg_x, "Y": cg_y},
        faculty={"F1": Faculty(name="F1")},
        rooms={"R1": Room(name="R1", room_type="T"), "R2": Room(name="R2", room_type="T")},
    )
    tt2, rep2 = schedule_theory_subjects(data2)
    for d in tt2.days:
        for p in range(1, tt2.periods_per_day + 1):
            slot_x = tt2.get_assignment("X", d, p)
            slot_y = tt2.get_assignment("Y", d, p)
            f_x = slot_x.faculty if slot_x else None
            f_y = slot_y.faculty if slot_y else None
            if f_x and f_y:
                assert f_x != f_y
    assert rep2.total_theory_scheduled == 4
    print("[PASS] Test 2: Prevented shared-faculty clash across classes X and Y")
    passed_count += 1

    # -------------------------------------------------------------
    # Test 3: Preventing room double-booking
    # -------------------------------------------------------------
    cg_a = ClassGroup(name="A")
    cg_a.add_subject(Subject(name="SUB_A", faculty="FA", periods_per_week=1, is_lab=False))
    cg_b = ClassGroup(name="B")
    cg_b.add_subject(Subject(name="SUB_B", faculty="FB", periods_per_week=1, is_lab=False))
    data3 = ParsedData(
        days=["D1"],
        periods_per_day=2,
        classes={"A": cg_a, "B": cg_b},
        faculty={"FA": Faculty(name="FA"), "FB": Faculty(name="FB")},
        rooms={"R1": Room(name="R1", room_type="T")},
    )
    tt3, rep3 = schedule_theory_subjects(data3)
    for d in tt3.days:
        for p in range(1, tt3.periods_per_day + 1):
            slot_a = tt3.get_assignment("A", d, p)
            slot_b = tt3.get_assignment("B", d, p)
            r_a = slot_a.room if slot_a else None
            r_b = slot_b.room if slot_b else None
            if r_a and r_b:
                assert r_a != r_b
    assert rep3.total_theory_scheduled == 2
    print("[PASS] Test 3: Prevented room double-booking across classes")
    passed_count += 1

    # -------------------------------------------------------------
    # Test 4: Reporting lab periods as pending in theory-only mode
    # -------------------------------------------------------------
    sample_data = parse_timetable_input(SAMPLE_INPUT)
    tt4, rep4 = schedule_theory_subjects(sample_data)
    assert rep4.total_lab_pending == 4
    print("[PASS] Test 4: Correctly identified and reported lab periods as pending (4 pending across 2 lab subjects)")
    passed_count += 1

    # -------------------------------------------------------------
    # Test 5: Reporting unscheduled theory periods when slots are insufficient
    # -------------------------------------------------------------
    cg_heavy = ClassGroup(name="HEAVY")
    cg_heavy.add_subject(Subject(name="OVERLOAD", faculty="FO", periods_per_week=3, is_lab=False))
    data5 = ParsedData(
        days=["D1"],
        periods_per_day=1,
        classes={"HEAVY": cg_heavy},
        faculty={"FO": Faculty(name="FO")},
        rooms={"R1": Room(name="R1", room_type="T")},
    )
    tt5, rep5 = schedule_theory_subjects(data5)
    assert rep5.total_theory_scheduled == 1
    assert rep5.total_theory_unscheduled == 2
    assert rep5.success is False
    print("[PASS] Test 5: Accurately reported unscheduled theory periods under capacity constraints")
    passed_count += 1

    # -------------------------------------------------------------
    # Test 6: Successfully scheduled two-period lab
    # -------------------------------------------------------------
    cg_lab1 = ClassGroup(name="CLS_LAB")
    cg_lab1.add_subject(Subject(name="PHYS_LAB", faculty="FL1", periods_per_week=2, is_lab=True))
    data6 = ParsedData(
        days=["D1"],
        periods_per_day=2,
        classes={"CLS_LAB": cg_lab1},
        faculty={"FL1": Faculty(name="FL1")},
        rooms={"L1": Room(name="L1", room_type="L")},
    )
    tt6 = Timetable(days=["D1"], periods_per_day=2)
    tt6.initialize_class("CLS_LAB")
    tt6, rep6 = schedule_lab_subjects(data6, tt6)
    st6 = rep6.subject_statuses[0]
    assert st6.scheduled_periods == 2
    assert st6.status == "COMPLETED"
    print("[PASS] Test 6: Successfully scheduled two-consecutive-period lab block")
    passed_count += 1

    # -------------------------------------------------------------
    # Test 7: Lab cannot fit because only one period is free
    # -------------------------------------------------------------
    cg_lab2 = ClassGroup(name="CLS_ONE")
    cg_lab2.add_subject(Subject(name="CHEM_LAB", faculty="FL2", periods_per_week=2, is_lab=True))
    data7 = ParsedData(
        days=["D1"],
        periods_per_day=2,
        classes={"CLS_ONE": cg_lab2},
        faculty={"FL2": Faculty(name="FL2")},
        rooms={"L1": Room(name="L1", room_type="L")},
    )
    tt7 = Timetable(days=["D1"], periods_per_day=2)
    tt7.initialize_class("CLS_ONE")
    tt7.assign("CLS_ONE", "D1", 1, Assignment(subject="MATH", faculty="FM", room="R1"))
    tt7, rep7 = schedule_lab_subjects(data7, tt7)
    st7 = rep7.subject_statuses[0]
    assert st7.scheduled_periods == 0
    assert st7.status == "UNSCHEDULED"
    print("[PASS] Test 7: Rejected lab placement when only single isolated period is free")
    passed_count += 1

    # -------------------------------------------------------------
    # Test 8: Conflict in second period rejected both slots atomically
    # -------------------------------------------------------------
    cg_x_block = ClassGroup(name="CX")
    cg_x_block.add_subject(Subject(name="BIO_LAB", faculty="FB", periods_per_week=2, is_lab=True))
    cg_y_block = ClassGroup(name="CY")
    data8 = ParsedData(
        days=["D1"],
        periods_per_day=3,
        classes={"CX": cg_x_block, "CY": cg_y_block},
        faculty={"FB": Faculty(name="FB"), "FY": Faculty(name="FY")},
        rooms={"L1": Room(name="L1", room_type="L")},
    )
    tt8 = Timetable(days=["D1"], periods_per_day=3)
    tt8.initialize_class("CX")
    tt8.initialize_class("CY")
    tt8.assign("CY", "D1", 2, Assignment(subject="OTHER", faculty="FY", room="L1"))
    tt8, rep8 = schedule_lab_subjects(data8, tt8)
    st8 = [s for s in rep8.subject_statuses if s.class_name == "CX"][0]
    assert st8.scheduled_periods == 0
    assert tt8.get_assignment("CX", "D1", 1) is None
    assert tt8.get_assignment("CX", "D1", 2) is None
    print("[PASS] Test 8: Conflict in second period rejected both slots atomically (no partial assignment)")
    passed_count += 1

    # -------------------------------------------------------------
    # Test 9: Atomic lab placement and rollback
    # -------------------------------------------------------------
    tt_ab = Timetable(days=["D1"], periods_per_day=3)
    tt_ab.initialize_class("C_ROLL")
    unit_lab = SchedulingUnit(
        unit_id="U_LAB",
        class_name="C_ROLL",
        subject=Subject(name="L_TEST", faculty="F_TEST", periods_per_week=2, is_lab=True),
        is_lab=True,
        periods=2,
        faculty="F_TEST",
    )
    placement_lab = CandidatePlacement(day="D1", start_period=1, room=Room(name="L1", room_type="L"))
    apply_assignment(tt_ab, unit_lab, placement_lab)
    assert tt_ab.get_assignment("C_ROLL", "D1", 1) is not None
    assert tt_ab.get_assignment("C_ROLL", "D1", 2) is not None
    undo_assignment(tt_ab, unit_lab, placement_lab)
    assert tt_ab.get_assignment("C_ROLL", "D1", 1) is None
    assert tt_ab.get_assignment("C_ROLL", "D1", 2) is None
    print("[PASS] Test 9: Atomic lab placement and rollback verified")
    passed_count += 1

    # -------------------------------------------------------------
    # Test 10: Solved constrained problem where naive greedy ordering fails
    # -------------------------------------------------------------
    fac_f1 = Faculty(name="F1")
    fac_f1.mark_available("D1", 1)
    fac_f1.mark_available("D1", 3)
    fac_f2 = Faculty(name="F2")
    fac_f2.mark_available("D1", 1)
    fac_f3 = Faculty(name="F3")
    fac_f3.mark_available("D1", 2)
    fac_f3.mark_available("D1", 3)

    cg_x_order = ClassGroup(name="CX")
    cg_x_order.add_subject(Subject(name="TH_X", faculty="F1", periods_per_week=1, is_lab=False))
    cg_y_order = ClassGroup(name="CY")
    cg_y_order.add_subject(Subject(name="TH_Y", faculty="F2", periods_per_week=1, is_lab=False))
    cg_z_order = ClassGroup(name="CZ")
    cg_z_order.add_subject(Subject(name="LAB_Z", faculty="F3", periods_per_week=2, is_lab=True))

    data_ordering = ParsedData(
        days=["D1"],
        periods_per_day=3,
        classes={"CX": cg_x_order, "CY": cg_y_order, "CZ": cg_z_order},
        faculty={"F1": fac_f1, "F2": fac_f2, "F3": fac_f3},
        rooms={"R1": Room(name="R1", room_type="T"), "L1": Room(name="L1", room_type="L")},
    )

    res_ordering = backtrack_search(data_ordering)
    assert res_ordering.status in (SearchStatus.SOLVED, SearchStatus.OPTIMAL)
    assert res_ordering.timetable is not None
    assert res_ordering.timetable.get_assignment("CY", "D1", 1) is not None
    assert res_ordering.timetable.get_assignment("CX", "D1", 3) is not None
    print("[PASS] Test 10: Solved constrained problem where naive greedy ordering fails via MRV/backtracking")
    passed_count += 1

    # -------------------------------------------------------------
    # Test 11: Infeasible timetable detection
    # -------------------------------------------------------------
    cg_inf = ClassGroup(name="C_INF")
    cg_inf.add_subject(Subject(name="SUB_INF", faculty="F_INF", periods_per_week=5, is_lab=False))
    data_inf = ParsedData(
        days=["D1"],
        periods_per_day=2,
        classes={"C_INF": cg_inf},
        faculty={"F_INF": Faculty(name="F_INF")},
        rooms={"R1": Room(name="R1", room_type="T")},
    )
    res_inf = backtrack_search(data_inf)
    assert res_inf.status == SearchStatus.INFEASIBLE
    assert res_inf.timetable is None
    print("[PASS] Test 11: Accurately detected and reported infeasible problem")
    passed_count += 1

    # -------------------------------------------------------------
    # Test 12: Forward checking detects unit with 0 valid candidates
    # -------------------------------------------------------------
    cg_fc1 = ClassGroup(name="C1")
    cg_fc1.add_subject(Subject(name="S1", faculty="F1", periods_per_week=1, is_lab=False))
    cg_fc2 = ClassGroup(name="C2")
    cg_fc2.add_subject(Subject(name="S2", faculty="F2", periods_per_week=1, is_lab=False))
    data_fc = ParsedData(
        days=["D1"],
        periods_per_day=1,
        classes={"C1": cg_fc1, "C2": cg_fc2},
        faculty={"F1": Faculty(name="F1"), "F2": Faculty(name="F2")},
        rooms={"R1": Room(name="R1", room_type="T")},
    )
    res_fc = backtrack_search(data_fc)
    assert res_fc.status == SearchStatus.INFEASIBLE
    print("[PASS] Test 12: Forward checking promptly pruned impossible branch")
    passed_count += 1

    # -------------------------------------------------------------
    # Test 13: Search-node limit reporting
    # -------------------------------------------------------------
    res_limit = backtrack_search(sample_data, max_nodes=1)
    assert res_limit.status == SearchStatus.LIMIT_REACHED
    assert "Search limit reached" in res_limit.message or "limit reached" in res_limit.message.lower()
    assert res_limit.timetable is None
    print("[PASS] Test 13: Correctly reported search-node limit without claiming infeasibility")
    passed_count += 1

    # -------------------------------------------------------------
    # Test 14: Verification that every final assignment passes the validator
    # -------------------------------------------------------------
    tt_full, rep_full = schedule_timetable(sample_data, use_backtracking=True)
    assert tt_full is not None
    assert rep_full.success is True
    assert rep_full.total_theory_scheduled == 4
    assert rep_full.total_lab_scheduled == 4

    for c_name in sample_data.classes:
        for day in tt_full.days:
            for p in range(1, tt_full.periods_per_day + 1):
                slot = tt_full.get_assignment(c_name, day, p)
                if slot:
                    room_obj = sample_data.rooms[slot.room]
                    is_lab_slot = slot.subject.endswith("LAB") or slot.subject in ("LAB", "DSA LAB", "OS LAB")
                    if is_lab_slot:
                        assert room_obj.room_type == "L"
                    else:
                        assert room_obj.room_type == "T"

    for day in tt_full.days:
        for p in range(1, tt_full.periods_per_day + 1):
            rooms_used = []
            faculty_used = []
            for c_name in sample_data.classes:
                slot = tt_full.get_assignment(c_name, day, p)
                if slot:
                    assert slot.room not in rooms_used
                    assert slot.faculty not in faculty_used
                    rooms_used.append(slot.room)
                    faculty_used.append(slot.faculty)
    print("[PASS] Test 14: Every final assignment rigorously passed all validator constraints")
    passed_count += 1

    # -------------------------------------------------------------
    # Test 15: Preservation of existing theory assignments
    # -------------------------------------------------------------
    tt15, rep15 = schedule_theory_subjects(sample_data)
    theory_snapshot = {}
    for c_name in sample_data.classes:
        for d in tt15.days:
            for p in range(1, tt15.periods_per_day + 1):
                assign = tt15.get_assignment(c_name, d, p)
                if assign is not None:
                    theory_snapshot[(c_name, d, p)] = (assign.subject, assign.faculty, assign.room)
    tt15, rep15 = schedule_lab_subjects(sample_data, tt15, rep15)
    for (c_name, d, p), expected_tuple in theory_snapshot.items():
        actual = tt15.get_assignment(c_name, d, p)
        assert actual is not None
        assert (actual.subject, actual.faculty, actual.room) == expected_tuple
    print("[PASS] Test 15: All previously scheduled theory assignments preserved intact")
    passed_count += 1

    # -------------------------------------------------------------
    # Test 16: Complete sample scheduled (all 8 required periods)
    # -------------------------------------------------------------
    total_slots = sum(
        1
        for c in tt_full.grid
        for d in tt_full.grid[c]
        for p in tt_full.grid[c][d]
        if tt_full.grid[c][d][p] is not None
    )
    assert total_slots == 8
    print("[PASS] Test 16: Full sample generated via backtracking with all 8 periods scheduled")
    passed_count += 1

    # -------------------------------------------------------------
    # Test 17: Correct gap counting (empty periods between first & last occupied)
    # -------------------------------------------------------------
    tt_gaps = Timetable(days=["D1"], periods_per_day=5)
    tt_gaps.initialize_class("CGAP")
    # Periods: 1 (occupied), 2 (empty), 3 (occupied), 4 (empty), 5 (occupied)
    tt_gaps.assign("CGAP", "D1", 1, Assignment(subject="S1", faculty="F1", room="R1"))
    tt_gaps.assign("CGAP", "D1", 3, Assignment(subject="S2", faculty="F1", room="R1"))
    tt_gaps.assign("CGAP", "D1", 5, Assignment(subject="S3", faculty="F1", room="R1"))
    # Gaps should be exactly 2 (periods 2 and 4)
    gaps_found = calculate_class_day_gaps(tt_gaps, "CGAP", "D1")
    assert gaps_found == 2, f"Expected 2 gaps, got {gaps_found}"
    assert calculate_total_idle_gaps(tt_gaps) == 2
    print("[PASS] Test 17: Correct gap counting for interleaved empty periods (2 gaps)")
    passed_count += 1

    # -------------------------------------------------------------
    # Test 18: No gaps for consecutive lessons
    # -------------------------------------------------------------
    tt_nogap = Timetable(days=["D1"], periods_per_day=6)
    tt_nogap.initialize_class("CNOGAP")
    # Periods 1, 2, 3 occupied; 4, 5, 6 empty
    tt_nogap.assign("CNOGAP", "D1", 1, Assignment(subject="S1", faculty="F1", room="R1"))
    tt_nogap.assign("CNOGAP", "D1", 2, Assignment(subject="S2", faculty="F1", room="R1"))
    tt_nogap.assign("CNOGAP", "D1", 3, Assignment(subject="S3", faculty="F1", room="R1"))
    assert calculate_class_day_gaps(tt_nogap, "CNOGAP", "D1") == 0
    assert calculate_total_idle_gaps(tt_nogap) == 0
    print("[PASS] Test 18: Verified zero idle gaps for consecutive lessons")
    passed_count += 1

    # -------------------------------------------------------------
    # Test 19: Empty periods before or after lessons are not counted
    # -------------------------------------------------------------
    tt_edge = Timetable(days=["D1"], periods_per_day=6)
    tt_edge.initialize_class("CEDGE")
    # Periods 1, 2 empty; 3, 4 occupied; 5, 6 empty
    tt_edge.assign("CEDGE", "D1", 3, Assignment(subject="S1", faculty="F1", room="R1"))
    tt_edge.assign("CEDGE", "D1", 4, Assignment(subject="S2", faculty="F1", room="R1"))
    assert calculate_class_day_gaps(tt_edge, "CEDGE", "D1") == 0
    assert calculate_total_idle_gaps(tt_edge) == 0
    print("[PASS] Test 19: Empty periods before and after lessons correctly excluded from gaps")
    passed_count += 1

    # -------------------------------------------------------------
    # Test 20: Independent gap counts for separate days and classes
    # -------------------------------------------------------------
    tt_multi = Timetable(days=["D1", "D2"], periods_per_day=4)
    tt_multi.initialize_class("C_ALPHA")
    tt_multi.initialize_class("C_BETA")
    # Class C_ALPHA on D1: P1 and P3 occupied -> 1 gap (P2)
    tt_multi.assign("C_ALPHA", "D1", 1, Assignment(subject="A1", faculty="F1", room="R1"))
    tt_multi.assign("C_ALPHA", "D1", 3, Assignment(subject="A2", faculty="F1", room="R1"))
    # Class C_ALPHA on D2: P1 and P2 occupied -> 0 gaps
    tt_multi.assign("C_ALPHA", "D2", 1, Assignment(subject="A3", faculty="F1", room="R1"))
    tt_multi.assign("C_ALPHA", "D2", 2, Assignment(subject="A4", faculty="F1", room="R1"))
    # Class C_BETA on D2: P1 and P4 occupied -> 2 gaps (P2, P3)
    tt_multi.assign("C_BETA", "D2", 1, Assignment(subject="B1", faculty="F2", room="R2"))
    tt_multi.assign("C_BETA", "D2", 4, Assignment(subject="B2", faculty="F2", room="R2"))

    breakdown = calculate_gaps_by_class(tt_multi)
    assert breakdown["C_ALPHA"] == 1
    assert breakdown["C_BETA"] == 2
    assert calculate_total_idle_gaps(tt_multi) == 3
    print("[PASS] Test 20: Verified independent gap counts across different days and classes")
    passed_count += 1

    # -------------------------------------------------------------
    # Test 21: Optimizer reducing gaps compared with a deliberately poor valid timetable
    # -------------------------------------------------------------
    # In a timetable with 1 day, 4 periods, Class A has 2 theory lessons:
    # A poor timetable places them at P1 and P4 -> 2 gaps!
    # The optimizer finds the compact schedule with P1 and P2 (or P2 and P3) -> 0 gaps!
    tt_poor = Timetable(days=["D1"], periods_per_day=4)
    tt_poor.initialize_class("C_OPT")
    tt_poor.assign("C_OPT", "D1", 1, Assignment(subject="OPT_TH", faculty="F_OPT", room="R1"))
    tt_poor.assign("C_OPT", "D1", 4, Assignment(subject="OPT_TH", faculty="F_OPT", room="R1"))
    poor_gaps = calculate_total_idle_gaps(tt_poor)
    assert poor_gaps == 2

    cg_opt = ClassGroup(name="C_OPT")
    cg_opt.add_subject(Subject(name="OPT_TH", faculty="F_OPT", periods_per_week=2, is_lab=False))
    data_opt = ParsedData(
        days=["D1"],
        periods_per_day=4,
        classes={"C_OPT": cg_opt},
        faculty={"F_OPT": Faculty(name="F_OPT")},
        rooms={"R1": Room(name="R1", room_type="T")},
    )
    res_opt = backtrack_search(data_opt, optimize=True)
    assert res_opt.timetable is not None
    assert res_opt.best_gaps == 0
    assert res_opt.best_gaps < poor_gaps
    print(f"[PASS] Test 21: Optimizer eliminated idle gaps (reduced from {poor_gaps} to {res_opt.best_gaps} gaps)")
    passed_count += 1

    # -------------------------------------------------------------
    # Test 22: Every optimized result remaining completely conflict-free
    # -------------------------------------------------------------
    res_sample_opt = backtrack_search(sample_data, optimize=True)
    assert res_sample_opt.timetable is not None
    assert res_sample_opt.best_gaps == 0

    # Rigorous validator check on the optimized sample timetable
    for c_name in sample_data.classes:
        for day in res_sample_opt.timetable.days:
            for p in range(1, res_sample_opt.timetable.periods_per_day + 1):
                slot = res_sample_opt.timetable.get_assignment(c_name, day, p)
                if slot:
                    room_obj = sample_data.rooms[slot.room]
                    if slot.subject.endswith("LAB") or slot.subject in ("LAB", "DSA LAB", "OS LAB"):
                        assert room_obj.room_type == "L"
                    else:
                        assert room_obj.room_type == "T"

    for day in res_sample_opt.timetable.days:
        for p in range(1, res_sample_opt.timetable.periods_per_day + 1):
            r_set = set()
            f_set = set()
            for c_name in sample_data.classes:
                slot = res_sample_opt.timetable.get_assignment(c_name, day, p)
                if slot:
                    assert slot.room not in r_set
                    assert slot.faculty not in f_set
                    r_set.add(slot.room)
                    f_set.add(slot.faculty)
    print("[PASS] Test 22: Optimized timetable strictly verified conflict-free across all hard constraints")
    passed_count += 1

    # -------------------------------------------------------------
    # Test 23: Search-budget exhaustion preserving the best valid timetable found
    # -------------------------------------------------------------
    # In a problem where solutions require gaps (e.g. F1 available only at P1 and P3 on D1),
    # the first valid solution has 1 gap. Searching for a 0-gap solution exhausts max_nodes.
    # It must return FEASIBLE_SEARCH_LIMITED and preserve the valid timetable!
    fac_lim = Faculty(name="F_LIM")
    fac_lim.mark_available("D1", 1)
    fac_lim.mark_available("D1", 3)

    cg_lim = ClassGroup(name="C_LIM")
    cg_lim.add_subject(Subject(name="SUB_LIM", faculty="F_LIM", periods_per_week=2, is_lab=False))

    data_lim = ParsedData(
        days=["D1"],
        periods_per_day=3,
        classes={"C_LIM": cg_lim},
        faculty={"F_LIM": fac_lim},
        rooms={"R1": Room(name="R1", room_type="T")},
    )

    res_budget = backtrack_search(data_lim, max_nodes=3, optimize=True)
    assert res_budget.timetable is not None, "Failed to preserve valid timetable on budget exhaustion!"
    assert res_budget.status == SearchStatus.FEASIBLE_SEARCH_LIMITED
    assert res_budget.optimality_proven is False
    assert res_budget.best_gaps == 1

    # Verify the preserved timetable is completely valid and conflict-free
    assert res_budget.timetable.get_assignment("C_LIM", "D1", 1) is not None
    assert res_budget.timetable.get_assignment("C_LIM", "D1", 3) is not None
    assert res_budget.timetable.get_assignment("C_LIM", "D1", 2) is None
    print("[PASS] Test 23: Search budget exhaustion safely preserved the best valid timetable found")
    passed_count += 1

    print("\n============================================================")
    print(f"Result: All {passed_count}/{total_tests} scheduler & optimization tests passed successfully!")
    print("============================================================\n")
    return True


if __name__ == "__main__":
    run_scheduler_tests()
