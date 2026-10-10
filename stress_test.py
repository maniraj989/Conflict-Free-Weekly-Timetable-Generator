"""10-Class, 5-Second Scalability and Performance Stress Test.

Constructs a realistic, challenging, and deterministic 10-class weekly timetable
benchmark containing:
- Exactly 10 student class groups (C1 to C10).
- Exactly 40 subject entries (4 per class: TH1, TH2, TH3, LAB).
- 30 weekly periods per class (5 days x 6 periods per day).
- Total curriculum periods: 300 periods across all 10 classes.
- 14 shared venues (10 Theory rooms R1-R10, 4 Lab rooms L1-L4).
- 20 shared instructors (F1-F20) with realistic staggered teaching loads (<= 20 periods/week).
- Strict adherence to the 5.0-second performance requirement measured via time.perf_counter().
- Rigorous independent validation of all 8 hard constraints.
"""

from collections import defaultdict
import time
from typing import List, Tuple
from models import Timetable
from parser import ParsedData, parse_timetable_input
from scheduler import schedule_timetable


def build_stress_test_input_text() -> str:
    """Generate the deterministic 10-class, 40-subject timetable input text.

    Curriculum per class (30 periods total, all using exact TH and LAB placeholders):
    - TH (Theory 1): 10 periods (is_lab=0)
    - TH (Theory 2): 10 periods (is_lab=0)
    - TH (Theory 3): 6 periods  (is_lab=0)
    - LAB (Laboratory): 4 periods (is_lab=1, 2 two-period blocks)

    Faculty allocation (20 instructors total, load <= 20 periods/week):
    - TH (1): F1 (C1, C2), F2 (C3, C4), F3 (C5, C6), F4 (C7, C8), F5 (C9, C10) -> 20 periods each
    - TH (2): F6 (C2, C3), F7 (C4, C5), F8 (C6, C7), F9 (C8, C9), F10 (C10, C1) -> 20 periods each
    - TH (3): F11 (C1, C4, C7), F12 (C2, C5, C8), F13 (C3, C6, C9) -> 18 periods each; F14 (C10) -> 6 periods
    - LAB:    F15 (C1, C2), F16 (C3, C4), F17 (C5, C6), F18 (C7, C8), F19 (C9, C10) -> 8 periods each

    Venues (14 rooms total):
    - 10 Theory classrooms: R1 through R10 (room_type 'T')
    - 4 Laboratories: L1 through L4 (room_type 'L')
    """
    lines = ["5 6", "40"]

    th1_fac = {1: "F1", 2: "F1", 3: "F2", 4: "F2", 5: "F3", 6: "F3", 7: "F4", 8: "F4", 9: "F5", 10: "F5"}
    th2_fac = {2: "F6", 3: "F6", 4: "F7", 5: "F7", 6: "F8", 7: "F8", 8: "F9", 9: "F9", 10: "F10", 1: "F10"}
    th3_fac = {1: "F11", 4: "F11", 7: "F11", 2: "F12", 5: "F12", 8: "F12", 3: "F13", 6: "F13", 9: "F13", 10: "F14"}
    lab_fac = {1: "F15", 2: "F15", 3: "F16", 4: "F16", 5: "F17", 6: "F17", 7: "F18", 8: "F18", 9: "F19", 10: "F19"}

    for i in range(1, 11):
        c = f"C{i}"
        lines.append(f"{c} TH {th1_fac[i]} 10 0")
        lines.append(f"{c} TH {th2_fac[i]} 10 0")
        lines.append(f"{c} TH {th3_fac[i]} 6 0")
        lines.append(f"{c} LAB {lab_fac[i]} 4 1")

    lines.append("14")
    for r in range(1, 11):
        lines.append(f"R{r} T")
    for l in range(1, 5):
        lines.append(f"L{l} L")

    return "\n".join(lines)


STRESS_TEST_INPUT = build_stress_test_input_text()


def validate_timetable_hard_constraints(
    timetable: Timetable, parsed_data: ParsedData
) -> Tuple[bool, List[str]]:
    """Independently validate generated timetable against every hard constraint.

    Checks:
    1. No teacher conflict (an instructor cannot teach multiple classes at the same period).
    2. No room conflict (a room cannot be double-booked during the same period).
    3. No class slot conflict (a student cohort cannot have multiple classes in the same period).
    4. Theory subjects use 'T' rooms.
    5. Laboratory subjects use 'L' rooms.
    6. Laboratory sessions occupy 2 consecutive periods on the same day with same faculty and room.
    7. All required weekly subject periods are scheduled.
    8. Faculty and room availability restrictions are satisfied.

    Returns:
        (is_valid: bool, errors: List[str])
    """
    errors: List[str] = []

    # 1, 2, 3: Exclusivity of faculty, room, and class at each slot
    for day in timetable.days:
        for p in range(1, timetable.periods_per_day + 1):
            faculty_seen = set()
            room_seen = set()
            for c_name in parsed_data.classes:
                slot = timetable.get_assignment(c_name, day, p)
                if slot is not None:
                    # Check teacher clash
                    if slot.faculty in faculty_seen:
                        errors.append(
                            f"Teacher clash: Faculty '{slot.faculty}' assigned to multiple classes at {day} Period {p}."
                        )
                    faculty_seen.add(slot.faculty)

                    # Check room clash
                    if slot.room in room_seen:
                        errors.append(
                            f"Room clash: Room '{slot.room}' double-booked at {day} Period {p}."
                        )
                    room_seen.add(slot.room)

                    # Check faculty availability
                    f_obj = parsed_data.faculty.get(slot.faculty)
                    if f_obj and not f_obj.is_available(day, p):
                        errors.append(
                            f"Faculty availability violation: Faculty '{slot.faculty}' unavailable at {day} Period {p}."
                        )

                    # Check room availability
                    r_obj = parsed_data.rooms.get(slot.room)
                    if r_obj and not r_obj.is_available(day, p):
                        errors.append(
                            f"Room availability violation: Room '{slot.room}' unavailable at {day} Period {p}."
                        )

    # 4 & 5: Room type matching
    for c_name in parsed_data.classes:
        for day in timetable.days:
            for p in range(1, timetable.periods_per_day + 1):
                slot = timetable.get_assignment(c_name, day, p)
                if slot is not None:
                    r_obj = parsed_data.rooms.get(slot.room)
                    if r_obj is None:
                        errors.append(f"Unknown room '{slot.room}' in assignment for class '{c_name}'.")
                        continue

                    is_lab_slot = (slot.subject == "LAB")
                    if is_lab_slot and r_obj.room_type != "L":
                        errors.append(
                            f"Room type mismatch: Lab session '{slot.subject}' assigned to room '{slot.room}' (type '{r_obj.room_type}')."
                        )
                    elif not is_lab_slot and r_obj.room_type != "T":
                        errors.append(
                            f"Room type mismatch: Theory session '{slot.subject}' assigned to room '{slot.room}' (type '{r_obj.room_type}')."
                        )

    # 6: Lab consecutive periods on same day with same room and instructor
    for c_name in parsed_data.classes:
        lab_slots_by_day = defaultdict(list)
        for day in timetable.days:
            for p in range(1, timetable.periods_per_day + 1):
                slot = timetable.get_assignment(c_name, day, p)
                if slot is not None and slot.subject == "LAB":
                    lab_slots_by_day[day].append((p, slot.faculty, slot.room))

        for day, slots in lab_slots_by_day.items():
            if len(slots) % 2 != 0:
                errors.append(
                    f"Class '{c_name}' on {day} has {len(slots)} lab periods (not a multiple of 2)."
                )
                continue

            slots.sort(key=lambda item: item[0])
            for idx in range(0, len(slots), 2):
                p1, f1, r1 = slots[idx]
                p2, f2, r2 = slots[idx + 1]
                if p2 != p1 + 1:
                    errors.append(
                        f"Non-consecutive lab block for class '{c_name}' on {day}: periods {p1} and {p2}."
                    )
                if f1 != f2:
                    errors.append(
                        f"Mismatched faculty in lab block for class '{c_name}' on {day}: {f1} vs {f2}."
                    )
                if r1 != r2:
                    errors.append(
                        f"Mismatched room in lab block for class '{c_name}' on {day}: {r1} vs {r2}."
                    )

    # 7: All required periods scheduled
    for c_name, c_group in parsed_data.classes.items():
        for subject in c_group.subjects:
            placed_count = sum(
                1
                for day in timetable.days
                for p in range(1, timetable.periods_per_day + 1)
                if (slot := timetable.get_assignment(c_name, day, p)) is not None
                and slot.subject == subject.name
                and slot.faculty == subject.faculty
            )
            if placed_count != subject.periods_per_week:
                errors.append(
                    f"Incomplete curriculum for class '{c_name}', subject '{subject.name}': "
                    f"expected {subject.periods_per_week} periods, got {placed_count}."
                )

    is_valid = len(errors) == 0
    return is_valid, errors


def run_stress_test(
    time_limit_seconds: float = 5.0,
    save_output_file: bool = True,
    output_path: str = "timetable_stress_output.txt",
) -> bool:
    """Execute the reproducible 10-class stress test, measure execution time, and validate results."""
    print("=" * 75)
    print("       Conflict-Free Weekly Timetable Generator - Scalability Stress Test")
    print("=" * 75)
    print("Benchmark Specification:")
    print(" - Class Cohorts:         10 classes (C1 through C10)")
    print(" - Subject Entries:       40 entries (4 per class: 3 TH entries, 1 LAB entry)")
    print(" - Time Grid:             5 days x 6 periods = 30 weekly periods per class")
    print(" - Total Workload:        300 curriculum periods across all classes")
    print(" - Resource Pools:        14 rooms (10 Theory, 4 Lab), 20 faculty (F1-F20)")
    print(f" - Performance Threshold: Completion within {time_limit_seconds:.1f} seconds")
    print("=" * 75)

    # 1. Parsing Phase
    t_parse_start = time.perf_counter()
    input_text = STRESS_TEST_INPUT
    parsed_data = parse_timetable_input(input_text)
    t_parse_end = time.perf_counter()
    parse_elapsed = t_parse_end - t_parse_start

    # 2. Scheduling Phase (Search + Backtracking + Forward Checking + Gap Optimization)
    t_sched_start = time.perf_counter()
    timetable, report = schedule_timetable(parsed_data, max_nodes=5000, optimize=True)
    t_sched_end = time.perf_counter()
    sched_elapsed = t_sched_end - t_sched_start

    # 3. Independent Hard Constraint Validation Phase
    t_val_start = time.perf_counter()
    if timetable is not None:
        validation_passed, validation_errors = validate_timetable_hard_constraints(
            timetable, parsed_data
        )
    else:
        validation_passed = False
        validation_errors = ["No timetable was generated by the scheduler."]
    t_val_end = time.perf_counter()
    val_elapsed = t_val_end - t_val_start

    total_elapsed = parse_elapsed + sched_elapsed + val_elapsed

    # Optional: Save generated stress test timetable to output file
    if save_output_file and timetable is not None:
        from scheduler import save_timetable_output
        save_timetable_output(output_path, timetable, report)

    # 4. Performance & Validation Evaluation
    within_time_limit = sched_elapsed <= time_limit_seconds
    total_required = report.total_theory_required + report.total_lab_required
    total_scheduled = report.total_theory_scheduled + report.total_lab_scheduled
    total_unscheduled = report.total_theory_unscheduled + report.total_lab_unscheduled

    pass_status = (
        report.success
        and timetable is not None
        and validation_passed
        and within_time_limit
    )

    # 5. Print Detailed Performance Report
    print("\n" + "=" * 75)
    print("                         STRESS TEST EXECUTION REPORT")
    print("=" * 75)
    print(f"Number of Classes:                 {len(parsed_data.classes)}")
    print(f"Number of Subject Entries:         {parsed_data.total_subject_entries}")
    print(f"Days and Periods per Day:          {len(parsed_data.days)} days x {parsed_data.periods_per_day} periods")
    print(f"Total Available Periods per Class: {len(parsed_data.days) * parsed_data.periods_per_day}")
    print(f"Total Required Subject Periods:    {total_required}")
    print(f"Total Scheduled Periods:           {total_scheduled}")
    print(f"Total Unscheduled Periods:         {total_unscheduled}")
    print(f"Search Status:                     {report.search_status}")
    print(f"Search Nodes Explored:             {report.diagnostics}")
    idle_gaps_str = f"{report.best_gaps} idle gaps" if report.best_gaps is not None else "N/A"
    print(f"Idle-Gap Count:                    {idle_gaps_str}")
    print(f"Parsing Elapsed Time:              {parse_elapsed:.4f} seconds")
    print(f"Scheduling Elapsed Time:           {sched_elapsed:.4f} seconds")
    print(f"Validation Elapsed Time:           {val_elapsed:.4f} seconds")
    print(f"Total End-to-End Elapsed Time:     {total_elapsed:.4f} seconds")
    print("-" * 75)
    print("Hard Constraint Validation:")
    if validation_passed:
        print(" [PASS] 1. No teacher double-booked in same period")
        print(" [PASS] 2. No room double-booked in same period")
        print(" [PASS] 3. No class slot overlaps")
        print(" [PASS] 4. Theory subjects placed in Theory rooms ('T')")
        print(" [PASS] 5. Lab subjects placed in Laboratory rooms ('L')")
        print(" [PASS] 6. Consecutive 2-period lab blocks with same room and faculty")
        print(" [PASS] 7. All 300 required weekly periods scheduled (100% complete)")
        print(" [PASS] 8. Faculty and room availability restrictions honored")
    else:
        print(f" [FAIL] Validation Errors ({len(validation_errors)}):")
        for err in validation_errors[:10]:
            print(f"   - {err}")
        if len(validation_errors) > 10:
            print(f"   ... and {len(validation_errors) - 10} more errors.")
    print("-" * 75)
    result_label = "PASS" if pass_status else "FAIL"
    print(f"Scalability Requirement (<= {time_limit_seconds:.1f}s): [{result_label}]")
    print(f" - Measured Scheduling Time: {sched_elapsed:.4f}s (Threshold: {time_limit_seconds:.1f}s)")
    if save_output_file and timetable is not None:
        print(f" - Timetable output saved to: '{output_path}'")
    print("=" * 75)

    return pass_status


if __name__ == "__main__":
    import sys
    success = run_stress_test()
    sys.exit(0 if success else 1)
