"""Conflict checker and placement validator for weekly timetables.

Provides constraint validation functions:
- can_place_subject: Validates single-period assignments against all hard constraints.
- can_place_lab_block: Validates 2-consecutive-period lab blocks on the same day.

Constraints checked:
1. Day and period validity within timetable bounds.
2. Target class slot is unoccupied.
3. Room type matches subject requirements (Theory -> 'T', Lab -> 'L').
4. Room is available (unconstrained or marked available).
5. Room is not assigned to another class in the same period (no room clash).
6. Faculty is available (unconstrained or marked available).
7. Faculty is not assigned to another class in the same period (no teacher clash).
"""

from typing import Optional, Tuple
from models import Assignment, Faculty, Room, Subject, Timetable


def can_place_subject(
    timetable: Timetable,
    class_name: str,
    day: str,
    period: int,
    subject: Subject,
    room: Room,
    faculty: Optional[Faculty] = None,
) -> Tuple[bool, str]:
    """Check whether a subject can be assigned to a specified class, day, period, and room.

    This function is strictly non-mutating and performs read-only constraint validation.

    Args:
        timetable: The Timetable instance holding current assignments.
        class_name: Name of the class group (e.g., 'X').
        day: Target day label (e.g., 'D1', 'Monday').
        period: Target period index (1-indexed).
        subject: The Subject to be placed.
        room: The proposed Room venue.
        faculty: Optional Faculty instance. If omitted, subject.faculty name is used.

    Returns:
        A tuple of (is_valid: bool, reason: str).
    """
    # 1. Check day bounds
    if day not in timetable.days:
        return (False, f"Invalid day '{day}'. Timetable days are: {timetable.days}")

    # 2. Check period bounds
    if period < 1 or period > timetable.periods_per_day:
        return (
            False,
            f"Invalid period '{period}'. Valid range is 1 to {timetable.periods_per_day}.",
        )

    # 3. Room type compatibility with subject requirement
    if not subject.is_lab and room.room_type != "T":
        return (
            False,
            f"Room type mismatch: Theory subject '{subject.name}' cannot be assigned to lab room '{room.name}' (type '{room.room_type}').",
        )

    if subject.is_lab and room.room_type != "L":
        return (
            False,
            f"Room type mismatch: Lab subject '{subject.name}' cannot be assigned to theory room '{room.name}' (type '{room.room_type}').",
        )

    # 4. Target class slot must be empty
    existing_assignment = timetable.get_assignment(class_name, day, period)
    if existing_assignment is not None:
        return (
            False,
            f"Class slot conflict: Class '{class_name}' already has an assignment at {day} Period {period} ('{existing_assignment.subject}').",
        )

    # 5. Room intrinsic availability
    if not room.is_available(day, period):
        return (
            False,
            f"Room availability conflict: Room '{room.name}' is marked unavailable at {day} Period {period}.",
        )

    # 6. Faculty intrinsic availability
    faculty_obj = faculty
    faculty_name = faculty_obj.name if faculty_obj else subject.faculty
    if faculty_obj is not None and not faculty_obj.is_available(day, period):
        return (
            False,
            f"Faculty availability conflict: Faculty '{faculty_name}' is marked unavailable at {day} Period {period}.",
        )

    # 7. Room clash: Room must not be booked by another class in the same period
    for other_class in timetable.grid:
        slot = timetable.get_assignment(other_class, day, period)
        if slot is not None and slot.room == room.name:
            return (
                False,
                f"Room clash: Room '{room.name}' is already assigned to class '{other_class}' at {day} Period {period}.",
            )

    # 8. Teacher clash: Faculty must not be booked by another class in the same period
    for other_class in timetable.grid:
        slot = timetable.get_assignment(other_class, day, period)
        if slot is not None and slot.faculty == faculty_name:
            return (
                False,
                f"Teacher clash: Faculty '{faculty_name}' is already assigned to class '{other_class}' at {day} Period {period}.",
            )

    return (True, "Valid assignment")


def can_place_lab_block(
    timetable: Timetable,
    class_name: str,
    day: str,
    start_period: int,
    subject: Subject,
    room: Room,
    faculty: Optional[Faculty] = None,
) -> Tuple[bool, str]:
    """Validate a two-consecutive-period laboratory placement on the same day.

    Checks:
    - Subject is designated as a lab subject (is_lab=True).
    - Consecutive periods (start_period and start_period + 1) fit within the timetable day.
    - All constraints are satisfied for both periods with the same room and faculty.

    Returns:
        A tuple of (is_valid: bool, reason: str).
    """
    if not subject.is_lab:
        return (False, f"Subject '{subject.name}' is not a laboratory subject (is_lab=False).")

    if day not in timetable.days:
        return (False, f"Invalid day '{day}'. Timetable days are: {timetable.days}")

    period1 = start_period
    period2 = start_period + 1

    if period1 < 1:
        return (False, f"Invalid start period '{start_period}'. Period must be >= 1.")

    if period2 > timetable.periods_per_day:
        return (
            False,
            f"Lab block starting at period {start_period} exceeds maximum daily periods ({timetable.periods_per_day}) on day '{day}'.",
        )

    # Validate first period
    ok1, reason1 = can_place_subject(
        timetable=timetable,
        class_name=class_name,
        day=day,
        period=period1,
        subject=subject,
        room=room,
        faculty=faculty,
    )
    if not ok1:
        return (False, f"Lab period 1 conflict ({day} Period {period1}): {reason1}")

    # Validate second period
    ok2, reason2 = can_place_subject(
        timetable=timetable,
        class_name=class_name,
        day=day,
        period=period2,
        subject=subject,
        room=room,
        faculty=faculty,
    )
    if not ok2:
        return (False, f"Lab period 2 conflict ({day} Period {period2}): {reason2}")

    return (
        True,
        f"Valid 2-period lab block assignment for periods {period1}-{period2} on {day}.",
    )


def run_validator_tests() -> bool:
    """Run all required unit tests for the timetable conflict checker.

    Tests:
    1. Valid theory assignment.
    2. Teacher conflict between classes X and Y using shared faculty F1.
    3. Room conflict between classes X and Y.
    4. Target class slot already occupied.
    5. Theory subject assigned to a lab room.
    6. Lab subject assigned to a theory room.
    7. Valid two-consecutive-period lab placement.
    8. Lab placement blocked by a conflict in its second period.
    9. Invalid day or period bounds.
    10. Immutability check (timetable unchanged by validations).
    """
    print("\n============================================================")
    print("           Running Timetable Validator Test Suite")
    print("============================================================\n")

    passed_count = 0
    total_tests = 10

    # Test Setup
    days = ["D1", "D2"]
    periods_per_day = 3
    timetable = Timetable(days=days, periods_per_day=periods_per_day)
    timetable.initialize_class("X")
    timetable.initialize_class("Y")

    fac_f1 = Faculty(name="F1")
    fac_f2 = Faculty(name="F2")
    fac_f3 = Faculty(name="F3")

    room_r1 = Room(name="R1", room_type="T")
    room_r2 = Room(name="R2", room_type="T")
    room_l1 = Room(name="L1", room_type="L")

    sub_th_x = Subject(name="TH", faculty="F1", periods_per_week=2, is_lab=False)
    sub_lab_x = Subject(name="LAB", faculty="F2", periods_per_week=2, is_lab=True)
    sub_th_y = Subject(name="TH", faculty="F1", periods_per_week=2, is_lab=False)
    sub_lab_y = Subject(name="LAB", faculty="F3", periods_per_week=2, is_lab=True)

    # -------------------------------------------------------------
    # Test 1: Valid theory assignment
    # -------------------------------------------------------------
    ok, reason = can_place_subject(
        timetable, "X", "D1", 1, sub_th_x, room_r1, fac_f1
    )
    assert ok is True, f"Test 1 failed: {reason}"
    print(f"[PASS] Test 1: Valid theory assignment -> {reason}")
    passed_count += 1

    # Place assignment in timetable for subsequent conflict tests:
    # Class X has TH with F1 in R1 at D1 Period 1
    timetable.assign(
        "X", "D1", 1, Assignment(subject="TH", faculty="F1", room="R1")
    )

    # -------------------------------------------------------------
    # Test 2: Teacher conflict between classes X and Y using shared faculty F1
    # -------------------------------------------------------------
    ok, reason = can_place_subject(
        timetable, "Y", "D1", 1, sub_th_y, room_r2, fac_f1
    )
    assert ok is False, "Test 2 failed: Should detect teacher clash"
    assert "Teacher clash" in reason and "F1" in reason, f"Unexpected message: {reason}"
    print(f"[PASS] Test 2: Teacher conflict detected -> {reason}")
    passed_count += 1

    # -------------------------------------------------------------
    # Test 3: Room conflict (Room R1 already used by Class X at D1 Period 1)
    # -------------------------------------------------------------
    sub_th_other = Subject(name="TH", faculty="F2", periods_per_week=2, is_lab=False)
    ok, reason = can_place_subject(
        timetable, "Y", "D1", 1, sub_th_other, room_r1, fac_f2
    )
    assert ok is False, "Test 3 failed: Should detect room clash"
    assert "Room clash" in reason and "R1" in reason, f"Unexpected message: {reason}"
    print(f"[PASS] Test 3: Room conflict detected -> {reason}")
    passed_count += 1

    # -------------------------------------------------------------
    # Test 4: Class slot already occupied (Class X at D1 Period 1)
    # -------------------------------------------------------------
    ok, reason = can_place_subject(
        timetable, "X", "D1", 1, sub_th_other, room_r2, fac_f2
    )
    assert ok is False, "Test 4 failed: Should detect class slot conflict"
    assert "Class slot conflict" in reason, f"Unexpected message: {reason}"
    print(f"[PASS] Test 4: Occupied class slot detected -> {reason}")
    passed_count += 1

    # -------------------------------------------------------------
    # Test 5: Theory subject assigned to a lab room (Room L1 is Lab)
    # -------------------------------------------------------------
    ok, reason = can_place_subject(
        timetable, "X", "D1", 2, sub_th_x, room_l1, fac_f1
    )
    assert ok is False, "Test 5 failed: Should detect theory assigned to lab"
    assert "Room type mismatch" in reason, f"Unexpected message: {reason}"
    print(f"[PASS] Test 5: Theory subject in lab room rejected -> {reason}")
    passed_count += 1

    # -------------------------------------------------------------
    # Test 6: Lab subject assigned to a theory room (Room R2 is Theory)
    # -------------------------------------------------------------
    ok, reason = can_place_subject(
        timetable, "X", "D1", 2, sub_lab_x, room_r2, fac_f2
    )
    assert ok is False, "Test 6 failed: Should detect lab assigned to theory room"
    assert "Room type mismatch" in reason, f"Unexpected message: {reason}"
    print(f"[PASS] Test 6: Lab subject in theory room rejected -> {reason}")
    passed_count += 1

    # -------------------------------------------------------------
    # Test 7: Valid two-consecutive-period lab placement (D1 Periods 2 and 3)
    # -------------------------------------------------------------
    ok, reason = can_place_lab_block(
        timetable, "X", "D1", 2, sub_lab_x, room_l1, fac_f2
    )
    assert ok is True, f"Test 7 failed: {reason}"
    print(f"[PASS] Test 7: Valid 2-period lab block -> {reason}")
    passed_count += 1

    # -------------------------------------------------------------
    # Test 8: Lab placement blocked by a conflict in its second period
    # Let's occupy period 3 of Class Y with Room L1
    # -------------------------------------------------------------
    timetable.assign(
        "Y", "D1", 3, Assignment(subject="LAB", faculty="F3", room="L1")
    )
    ok, reason = can_place_lab_block(
        timetable, "X", "D1", 2, sub_lab_x, room_l1, fac_f2
    )
    assert ok is False, "Test 8 failed: Should detect conflict in period 2 of block"
    assert "Lab period 2 conflict" in reason and "Room clash" in reason, f"Unexpected message: {reason}"
    print(f"[PASS] Test 8: Lab block blocked in 2nd period -> {reason}")
    passed_count += 1

    # -------------------------------------------------------------
    # Test 9: Invalid day or period bounds
    # -------------------------------------------------------------
    # 9a: Invalid day
    ok, reason = can_place_subject(
        timetable, "X", "D99", 1, sub_th_x, room_r1, fac_f1
    )
    assert ok is False and "Invalid day" in reason, f"9a failed: {reason}"

    # 9b: Invalid period (period 0)
    ok, reason = can_place_subject(
        timetable, "X", "D1", 0, sub_th_x, room_r1, fac_f1
    )
    assert ok is False and "Invalid period" in reason, f"9b failed: {reason}"

    # 9c: Lab block exceeding daily periods (start period 3 when max is 3)
    ok, reason = can_place_lab_block(
        timetable, "X", "D1", 3, sub_lab_x, room_l1, fac_f2
    )
    assert ok is False and "exceeds maximum daily periods" in reason, f"9c failed: {reason}"
    print(f"[PASS] Test 9: Invalid day/period/bounds correctly rejected")
    passed_count += 1

    # -------------------------------------------------------------
    # Test 10: Non-mutation verification
    # Verify timetable only contains the 2 explicitly placed test assignments
    # -------------------------------------------------------------
    assert timetable.get_assignment("X", "D1", 1) is not None
    assert timetable.get_assignment("Y", "D1", 3) is not None
    assert timetable.get_assignment("X", "D1", 2) is None
    assert timetable.get_assignment("X", "D1", 3) is None
    assert timetable.get_assignment("Y", "D1", 1) is None
    assert timetable.get_assignment("Y", "D1", 2) is None
    print(f"[PASS] Test 10: Timetable remains unmodified by validator checks")
    passed_count += 1

    print("\n============================================================")
    print(f"Result: All {passed_count}/{total_tests} validator tests passed successfully!")
    print("============================================================\n")
    return True


if __name__ == "__main__":
    run_validator_tests()
