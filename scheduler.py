"""Greedy scheduler for theory and laboratory subjects in weekly timetables.

Schedules theory subjects into individual single-period slots and laboratory
subjects into 2-consecutive-period blocks on the same day in compatible lab
rooms using the non-mutating conflict validator (validator.py).
"""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from models import Assignment, Timetable
from parser import ParsedData
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


@dataclass
class SchedulingReport:
    """Summary of timetable scheduling execution."""

    total_theory_required: int = 0
    total_theory_scheduled: int = 0
    total_theory_unscheduled: int = 0
    total_lab_required: int = 0
    total_lab_scheduled: int = 0
    total_lab_unscheduled: int = 0
    total_lab_pending: int = 0
    subject_statuses: List[SubjectScheduleStatus] = field(default_factory=list)
    success: bool = True


def schedule_theory_subjects(
    parsed_data: ParsedData, timetable: Optional[Timetable] = None
) -> Tuple[Timetable, SchedulingReport]:
    """Greedily schedule all theory subjects into valid timetable slots.

    For each class and each theory subject:
    - Attempts to schedule periods_per_week individual periods.
    - Searches days and periods systematically.
    - Tries compatible theory rooms (room_type == 'T').
    - Calls can_place_subject() from validator.py before every assignment.
    - Inserts Assignment only when validation succeeds.
    - Laboratory subjects are tracked as pending.

    Args:
        parsed_data: ParsedData containing classes, rooms, faculty, and grid dimensions.
        timetable: Optional existing Timetable. If None, a new one is initialized.

    Returns:
        A tuple of (Timetable, SchedulingReport).
    """
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
    )

    return timetable, report


def schedule_lab_subjects(
    parsed_data: ParsedData,
    timetable: Timetable,
    report: Optional[SchedulingReport] = None,
) -> Tuple[Timetable, SchedulingReport]:
    """Schedule laboratory subjects into 2-consecutive-period blocks on the same day.

    Requirements enforced:
    - Each lab requires 2 consecutive periods on the same day in the same lab room.
    - Atomic validation: Both periods must be validated via can_place_lab_block()
      before either assignment is inserted.
    - Uses exclusively laboratory rooms (room_type == 'L').
    - Preserves all previously scheduled theory assignments without overwriting.
    - Each lab block counts as 2 occupied periods in the report.

    Args:
        parsed_data: The parsed input data.
        timetable: The timetable already containing theory assignments.
        report: Optional existing SchedulingReport to update.

    Returns:
        A tuple of (Timetable, SchedulingReport).
    """
    if report is None:
        report = SchedulingReport()

    lab_rooms = [r for r in parsed_data.rooms.values() if r.room_type == "L"]

    total_lab_required = 0
    total_lab_scheduled = 0
    total_lab_unscheduled = 0

    # Build a lookup of existing statuses by (class_name, subject_name)
    existing_status_map = {
        (s.class_name, s.subject_name): s for s in report.subject_statuses
    }

    updated_statuses: List[SubjectScheduleStatus] = []

    for class_name, class_group in parsed_data.classes.items():
        for subject in class_group.subjects:
            if not subject.is_lab:
                # Keep existing theory status
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

            # Search for 2-consecutive-period blocks
            for _ in range(needed_blocks):
                block_placed = False

                for day in timetable.days:
                    if block_placed:
                        break

                    # Check each possible starting period (must leave room for start_period + 1)
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
                                # Atomic assignment: insert both consecutive slots
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
) -> Tuple[Timetable, SchedulingReport]:
    """Execute complete timetable generation for both theory and lab subjects.

    1. Schedules theory subjects into conflict-free single periods.
    2. Schedules laboratory subjects into conflict-free 2-period consecutive blocks.
    3. Preserves all previously placed assignments without overwriting.

    Args:
        parsed_data: ParsedData configuration.

    Returns:
        A tuple of (Timetable, SchedulingReport).
    """
    timetable, report = schedule_theory_subjects(parsed_data)
    timetable, report = schedule_lab_subjects(parsed_data, timetable, report)
    return timetable, report


def print_timetable_grid(timetable: Timetable) -> None:
    """Print a clean, tabular view of the timetable for each class."""
    print("\n" + "=" * 70)
    print("                    GENERATED TIMETABLE GRID")
    print("=" * 70)

    for class_name, days_dict in timetable.grid.items():
        print(f"\n--- Timetable for Class Group: {class_name} ---")

        col_width = 22
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
                    cell_text = f"{slot.subject} ({slot.faculty}, {slot.room})"
                else:
                    cell_text = "---"
                row_cells.append(cell_text.center(col_width))
            print(f"{day:<6} | " + " | ".join(row_cells))


def print_scheduling_report(report: SchedulingReport) -> None:
    """Print an organized breakdown of scheduling results."""
    print("\n" + "=" * 70)
    print("                 SCHEDULING SUMMARY REPORT")
    print("=" * 70)

    print("\n1. Theory Subjects:")
    print(f"   - Total Required:    {report.total_theory_required} periods")
    print(f"   - Total Scheduled:   {report.total_theory_scheduled} periods")
    print(f"   - Total Unscheduled: {report.total_theory_unscheduled} periods")

    print("\n2. Laboratory Subjects:")
    print(f"   - Total Required:    {report.total_lab_required} periods")
    print(f"   - Total Scheduled:   {report.total_lab_scheduled} periods")
    print(f"   - Total Unscheduled: {report.total_lab_unscheduled} periods")

    total_req = report.total_theory_required + report.total_lab_required
    total_sched = report.total_theory_scheduled + report.total_lab_scheduled
    total_unsched = (
        report.total_theory_unscheduled + report.total_lab_unscheduled
    )

    print("\n3. Overall Timetable Summary:")
    print(f"   - Total Required:    {total_req} periods")
    print(f"   - Total Scheduled:   {total_sched} periods")
    print(f"   - Total Unscheduled: {total_unsched} periods")
    status_label = (
        "COMPLETE (Conflict-Free)" if total_unsched == 0 else "INCOMPLETE"
    )
    print(f"   - Final Status:      {status_label}")

    print("\n4. Detailed Breakdown by Subject:")
    for status in report.subject_statuses:
        type_str = "Lab" if status.is_lab else "Theory"
        ratio = f"{status.scheduled_periods}/{status.required_periods}"
        print(
            f"   - [{status.class_name}] {status.subject_name:<5} ({type_str:<6}) | "
            f"Faculty: {status.faculty:<3} | {ratio} placed [{status.status}]"
        )
        if status.notes:
            print(f"     Note: {status.notes}")


def run_scheduler_tests() -> bool:
    """Run comprehensive unit tests for both theory and laboratory scheduling."""
    print("\n============================================================")
    print("           Running Timetable Scheduler Test Suite")
    print("============================================================\n")

    passed_count = 0
    total_tests = 13

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
    assert status_c1.remaining_periods == 0
    assert rep1.total_theory_scheduled == 2
    print("[PASS] Test 1: Scheduled theory subject for all required weekly periods (2/2)")
    passed_count += 1

    # -------------------------------------------------------------
    # Test 2: Preventing shared-faculty clash between classes X and Y
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
    lab_statuses = [s for s in rep4.subject_statuses if s.is_lab]
    assert len(lab_statuses) == 2
    for ls in lab_statuses:
        assert ls.status == "PENDING_LAB"
        assert ls.scheduled_periods == 0
        assert ls.required_periods == 2
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
    assert st6.scheduled_periods == 2, f"Expected 2 scheduled, got {st6.scheduled_periods}"
    assert st6.remaining_periods == 0
    assert st6.status == "COMPLETED"
    slot_p1 = tt6.get_assignment("CLS_LAB", "D1", 1)
    slot_p2 = tt6.get_assignment("CLS_LAB", "D1", 2)
    assert slot_p1 is not None and slot_p1.subject == "PHYS_LAB" and slot_p1.room == "L1"
    assert slot_p2 is not None and slot_p2.subject == "PHYS_LAB" and slot_p2.room == "L1"
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
    # Occupy Period 1 with a theory lesson
    tt7.assign("CLS_ONE", "D1", 1, Assignment(subject="MATH", faculty="FM", room="R1"))
    tt7, rep7 = schedule_lab_subjects(data7, tt7)
    st7 = rep7.subject_statuses[0]
    assert st7.scheduled_periods == 0
    assert st7.remaining_periods == 2
    assert st7.status == "UNSCHEDULED"
    assert tt7.get_assignment("CLS_ONE", "D1", 2) is None
    print("[PASS] Test 7: Rejected lab placement when only single isolated period is free")
    passed_count += 1

    # -------------------------------------------------------------
    # Test 8: Conflict in second period causes atomic rejection (neither slot assigned)
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
    # Class CY occupies room L1 at Period 2
    tt8.assign("CY", "D1", 2, Assignment(subject="OTHER", faculty="FY", room="L1"))
    tt8, rep8 = schedule_lab_subjects(data8, tt8)
    st8 = [s for s in rep8.subject_statuses if s.class_name == "CX"][0]
    # Period 1 was free, Period 2 was busy -> CX must NOT be placed in Period 1
    assert st8.scheduled_periods == 0
    assert tt8.get_assignment("CX", "D1", 1) is None
    assert tt8.get_assignment("CX", "D1", 2) is None
    print("[PASS] Test 8: Conflict in second period rejected both slots atomically (no partial assignment)")
    passed_count += 1

    # -------------------------------------------------------------
    # Test 9: Shared faculty conflict for lab subjects
    # -------------------------------------------------------------
    cg_f_a = ClassGroup(name="CA")
    cg_f_a.add_subject(Subject(name="LAB_A", faculty="F_SHARED", periods_per_week=2, is_lab=True))
    cg_f_b = ClassGroup(name="CB")
    cg_f_b.add_subject(Subject(name="LAB_B", faculty="F_SHARED", periods_per_week=2, is_lab=True))
    data9 = ParsedData(
        days=["D1"],
        periods_per_day=2,
        classes={"CA": cg_f_a, "CB": cg_f_b},
        faculty={"F_SHARED": Faculty(name="F_SHARED")},
        rooms={"L1": Room(name="L1", room_type="L"), "L2": Room(name="L2", room_type="L")},
    )
    tt9 = Timetable(days=["D1"], periods_per_day=2)
    tt9.initialize_class("CA")
    tt9.initialize_class("CB")
    tt9, rep9 = schedule_lab_subjects(data9, tt9)
    st_ca = [s for s in rep9.subject_statuses if s.class_name == "CA"][0]
    st_cb = [s for s in rep9.subject_statuses if s.class_name == "CB"][0]
    assert st_ca.scheduled_periods == 2
    assert st_cb.scheduled_periods == 0
    assert rep9.total_lab_scheduled == 2
    assert rep9.total_lab_unscheduled == 2
    print("[PASS] Test 9: Prevented shared-faculty collision between two laboratory classes")
    passed_count += 1

    # -------------------------------------------------------------
    # Test 10: Room conflict between two laboratory classes
    # -------------------------------------------------------------
    cg_r_a = ClassGroup(name="CRA")
    cg_r_a.add_subject(Subject(name="LAB_A", faculty="FA", periods_per_week=2, is_lab=True))
    cg_r_b = ClassGroup(name="CRB")
    cg_r_b.add_subject(Subject(name="LAB_B", faculty="FB", periods_per_week=2, is_lab=True))
    data10 = ParsedData(
        days=["D1"],
        periods_per_day=2,
        classes={"CRA": cg_r_a, "CRB": cg_r_b},
        faculty={"FA": Faculty(name="FA"), "FB": Faculty(name="FB")},
        rooms={"L1": Room(name="L1", room_type="L")},  # Only 1 lab room
    )
    tt10 = Timetable(days=["D1"], periods_per_day=2)
    tt10.initialize_class("CRA")
    tt10.initialize_class("CRB")
    tt10, rep10 = schedule_lab_subjects(data10, tt10)
    st_cra = [s for s in rep10.subject_statuses if s.class_name == "CRA"][0]
    st_crb = [s for s in rep10.subject_statuses if s.class_name == "CRB"][0]
    assert st_cra.scheduled_periods == 2
    assert st_crb.scheduled_periods == 0
    print("[PASS] Test 10: Prevented room double-booking in laboratory venues")
    passed_count += 1

    # -------------------------------------------------------------
    # Test 11: Insufficient lab rooms (only theory rooms available)
    # -------------------------------------------------------------
    cg_no_lab = ClassGroup(name="CNL")
    cg_no_lab.add_subject(Subject(name="CS_LAB", faculty="FC", periods_per_week=2, is_lab=True))
    data11 = ParsedData(
        days=["D1"],
        periods_per_day=2,
        classes={"CNL": cg_no_lab},
        faculty={"FC": Faculty(name="FC")},
        rooms={"R1": Room(name="R1", room_type="T")},  # No lab room
    )
    tt11 = Timetable(days=["D1"], periods_per_day=2)
    tt11.initialize_class("CNL")
    tt11, rep11 = schedule_lab_subjects(data11, tt11)
    st_cnl = rep11.subject_statuses[0]
    assert st_cnl.scheduled_periods == 0
    assert st_cnl.remaining_periods == 2
    assert "No compatible laboratory room available" in st_cnl.notes
    print("[PASS] Test 11: Correctly handled scenario with insufficient lab rooms")
    passed_count += 1

    # -------------------------------------------------------------
    # Test 12: Preservation of existing theory assignments
    # -------------------------------------------------------------
    tt12, rep12 = schedule_theory_subjects(sample_data)
    # Record theory assignments snapshot
    theory_snapshot = {}
    for c_name in sample_data.classes:
        for d in tt12.days:
            for p in range(1, tt12.periods_per_day + 1):
                assign = tt12.get_assignment(c_name, d, p)
                if assign is not None:
                    theory_snapshot[(c_name, d, p)] = (assign.subject, assign.faculty, assign.room)

    # Now schedule labs on top of existing timetable
    tt12, rep12 = schedule_lab_subjects(sample_data, tt12, rep12)

    # Verify every theory assignment is preserved intact
    for (c_name, d, p), expected_tuple in theory_snapshot.items():
        actual = tt12.get_assignment(c_name, d, p)
        assert actual is not None, f"Theory assignment at {c_name} {d} P{p} was erased!"
        assert (actual.subject, actual.faculty, actual.room) == expected_tuple, "Theory assignment was modified!"
    print("[PASS] Test 12: All previously scheduled theory assignments preserved intact")
    passed_count += 1

    # -------------------------------------------------------------
    # Test 13: Correct scheduled and unscheduled period counts for full sample
    # -------------------------------------------------------------
    tt13, rep13 = schedule_timetable(sample_data)
    assert rep13.total_theory_required == 4
    assert rep13.total_theory_scheduled == 4
    assert rep13.total_theory_unscheduled == 0
    assert rep13.total_lab_required == 4
    assert rep13.total_lab_scheduled == 4
    assert rep13.total_lab_unscheduled == 0
    assert rep13.total_lab_pending == 0
    assert rep13.success is True

    # Count total assigned slots in grid
    total_slots = sum(
        1
        for c in tt13.grid
        for d in tt13.grid[c]
        for p in tt13.grid[c][d]
        if tt13.grid[c][d][p] is not None
    )
    assert total_slots == 8, f"Expected 8 total scheduled slots (4 theory + 4 lab), got {total_slots}"
    print("[PASS] Test 13: Full sample generated with exact scheduled counts (4/4 theory, 4/4 lab, 8 total slots)")
    passed_count += 1

    print("\n============================================================")
    print(f"Result: All {passed_count}/{total_tests} scheduler tests passed successfully!")
    print("============================================================\n")
    return True


if __name__ == "__main__":
    run_scheduler_tests()
