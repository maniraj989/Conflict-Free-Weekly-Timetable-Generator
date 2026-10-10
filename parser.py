"""Input parser for the Conflict-Free Weekly Timetable Generator.

Parses timetable scheduling input following the defined format:
Line 1: days periods_per_day
Line 2: n (number of subject entries)
Next n lines: class_name subject_name faculty_name periods_per_week is_lab
Next line: m (number of rooms)
Next m lines: room_name room_type

room_type is 'T' for theory or 'L' for laboratory.
is_lab is 0 for theory and 1 for laboratory.
"""

from dataclasses import dataclass, field
from typing import Dict, List
from models import ClassGroup, Faculty, Room, Subject

SAMPLE_INPUT = """2 3
4
X TH F1 2 0
X LAB F2 2 1
Y TH F1 2 0
Y LAB F3 2 1
3
R1 T
R2 T
L1 L"""


@dataclass
class ParsedData:
    """Container for parsed timetable configuration data.

    Attributes:
        days: List of day labels (e.g. ['D1', 'D2']).
        periods_per_day: Number of periods per day.
        classes: Mapping of class name to ClassGroup object.
        faculty: Mapping of faculty name to unique Faculty object.
        rooms: Mapping of room name to Room object.
    """

    days: List[str]
    periods_per_day: int
    classes: Dict[str, ClassGroup] = field(default_factory=dict)
    faculty: Dict[str, Faculty] = field(default_factory=dict)
    rooms: Dict[str, Room] = field(default_factory=dict)

    @property
    def total_subject_entries(self) -> int:
        """Total number of subject entries across all class groups."""
        return sum(len(cg.subjects) for cg in self.classes.values())


def parse_timetable_input(input_text: str) -> ParsedData:
    """Parse timetable input text and return structured ParsedData.

    Validates:
    - Missing or incomplete lines
    - Integer conversions and positive value constraints
    - Non-negative period counts
    - Valid boolean flags for is_lab (0 or 1)
    - Valid room types ('T' or 'L')
    - Unexpected trailing input

    Raises:
        ValueError: If input format or values are invalid.
    """
    raw_lines = input_text.splitlines()
    indexed_lines = [
        (line_idx + 1, line.strip())
        for line_idx, line in enumerate(raw_lines)
        if line.strip()
    ]

    if not indexed_lines:
        raise ValueError("Input is empty. Expected timetable configuration on line 1.")

    current_idx = 0

    # 1. Parse days and periods_per_day
    line_num, line_content = indexed_lines[current_idx]
    tokens = line_content.split()
    if len(tokens) != 2:
        raise ValueError(
            f"Line {line_num}: Expected 2 values (days, periods_per_day), got {len(tokens)}: '{line_content}'"
        )

    try:
        num_days = int(tokens[0])
    except ValueError:
        raise ValueError(f"Line {line_num}: 'days' must be an integer, got '{tokens[0]}'.")

    try:
        periods_per_day = int(tokens[1])
    except ValueError:
        raise ValueError(f"Line {line_num}: 'periods_per_day' must be an integer, got '{tokens[1]}'.")

    if num_days <= 0:
        raise ValueError(f"Line {line_num}: 'days' must be a positive integer (> 0), got {num_days}.")
    if periods_per_day <= 0:
        raise ValueError(
            f"Line {line_num}: 'periods_per_day' must be a positive integer (> 0), got {periods_per_day}."
        )

    days = [f"D{i}" for i in range(1, num_days + 1)]
    current_idx += 1

    # 2. Parse n (number of subject entries)
    if current_idx >= len(indexed_lines):
        raise ValueError("Unexpected end of input: Missing number of subject entries 'n'.")

    line_num, line_content = indexed_lines[current_idx]
    tokens = line_content.split()
    if len(tokens) != 1:
        raise ValueError(
            f"Line {line_num}: Expected single integer for subject count 'n', got '{line_content}'."
        )

    try:
        num_subjects = int(tokens[0])
    except ValueError:
        raise ValueError(f"Line {line_num}: Subject count 'n' must be an integer, got '{tokens[0]}'.")

    if num_subjects < 0:
        raise ValueError(f"Line {line_num}: Subject count 'n' cannot be negative, got {num_subjects}.")

    current_idx += 1

    # 3. Parse n subject lines
    classes: Dict[str, ClassGroup] = {}
    faculty: Dict[str, Faculty] = {}

    for entry_idx in range(num_subjects):
        if current_idx >= len(indexed_lines):
            raise ValueError(
                f"Unexpected end of input: Expected {num_subjects} subject entries, but only found {entry_idx}."
            )

        line_num, line_content = indexed_lines[current_idx]
        tokens = line_content.split()
        if len(tokens) < 5:
            raise ValueError(
                f"Line {line_num}: Expected at least 5 values (class_name subject_name faculty_name periods_per_week is_lab), "
                f"got {len(tokens)}: '{line_content}'"
            )

        class_name = tokens[0]
        faculty_name = tokens[-3]
        periods_str = tokens[-2]
        is_lab_str = tokens[-1]
        subject_name = " ".join(tokens[1:-3])

        try:
            periods_per_week = int(periods_str)
        except ValueError:
            raise ValueError(f"Line {line_num}: 'periods_per_week' must be an integer, got '{periods_str}'.")

        if periods_per_week <= 0:
            raise ValueError(
                f"Line {line_num}: 'periods_per_week' must be a positive integer (> 0), got {periods_per_week}."
            )

        if is_lab_str not in ("0", "1"):
            raise ValueError(
                f"Line {line_num}: 'is_lab' must be 0 (Theory) or 1 (Lab), got '{is_lab_str}'."
            )
        is_lab = is_lab_str == "1"

        # Reuse single Faculty object for each unique faculty name
        if faculty_name not in faculty:
            faculty[faculty_name] = Faculty(name=faculty_name)

        subject = Subject(
            name=subject_name,
            faculty=faculty[faculty_name].name,
            periods_per_week=periods_per_week,
            is_lab=is_lab,
        )

        if class_name not in classes:
            classes[class_name] = ClassGroup(name=class_name)
        classes[class_name].add_subject(subject)

        current_idx += 1

    # 4. Parse m (number of rooms)
    if current_idx >= len(indexed_lines):
        raise ValueError("Unexpected end of input: Missing number of rooms 'm'.")

    line_num, line_content = indexed_lines[current_idx]
    tokens = line_content.split()
    if len(tokens) != 1:
        raise ValueError(
            f"Line {line_num}: Expected single integer for room count 'm', got '{line_content}'."
        )

    try:
        num_rooms = int(tokens[0])
    except ValueError:
        raise ValueError(f"Line {line_num}: Room count 'm' must be an integer, got '{tokens[0]}'.")

    if num_rooms < 0:
        raise ValueError(f"Line {line_num}: Room count 'm' cannot be negative, got {num_rooms}.")

    current_idx += 1

    # 5. Parse m room lines
    rooms: Dict[str, Room] = {}

    for room_idx in range(num_rooms):
        if current_idx >= len(indexed_lines):
            raise ValueError(
                f"Unexpected end of input: Expected {num_rooms} room entries, but only found {room_idx}."
            )

        line_num, line_content = indexed_lines[current_idx]
        tokens = line_content.split()
        if len(tokens) != 2:
            raise ValueError(
                f"Line {line_num}: Expected 2 values (room_name room_type), got {len(tokens)}: '{line_content}'"
            )

        room_name, room_type_token = tokens
        room_type_upper = room_type_token.upper()

        if room_type_upper not in ("T", "L"):
            raise ValueError(
                f"Line {line_num}: Invalid room_type '{room_type_token}'. Must be 'T' (Theory) or 'L' (Lab)."
            )

        rooms[room_name] = Room(name=room_name, room_type=room_type_upper)
        current_idx += 1

    # 6. Check for unexpected trailing input
    if current_idx < len(indexed_lines):
        extra_line_num, extra_content = indexed_lines[current_idx]
        raise ValueError(
            f"Line {extra_line_num}: Unexpected extra input after parsing {num_rooms} rooms: '{extra_content}'."
        )

    return ParsedData(
        days=days,
        periods_per_day=periods_per_day,
        classes=classes,
        faculty=faculty,
        rooms=rooms,
    )


def print_parsed_summary(data: ParsedData) -> None:
    """Print an organized, human-readable summary of the parsed timetable data."""
    print("\n--- Parsed Timetable Configuration Summary ---")
    print("\n1. Time Grid:")
    print(f"   - Days ({len(data.days)}): {data.days}")
    print(f"   - Periods per Day: {data.periods_per_day}")
    print(f"   - Total Slots per Class: {len(data.days) * data.periods_per_day}")

    print(
        f"\n2. Classes & Curriculum ({len(data.classes)} classes, {data.total_subject_entries} subject entries):"
    )
    for class_name, group in data.classes.items():
        print(f"   - Class '{class_name}' ({len(group.subjects)} subjects):")
        for sub in group.subjects:
            type_str = "Lab" if sub.is_lab else "Theory"
            print(
                f"     * {sub.name:<4} [{type_str:<6}] | Faculty: {sub.faculty} | {sub.periods_per_week} periods/week"
            )

    print(f"\n3. Faculty ({len(data.faculty)} unique instructors):")
    for f_name in sorted(data.faculty.keys()):
        associated_classes = [
            c_name
            for c_name, cg in data.classes.items()
            if any(s.faculty == f_name for s in cg.subjects)
        ]
        classes_str = ", ".join(associated_classes)
        note = " (SHARED across classes)" if len(associated_classes) > 1 else ""
        print(f"   - {f_name}: Assigned to class(es) [{classes_str}]{note}")

    print(f"\n4. Rooms ({len(data.rooms)} venues):")
    for r_name, room in data.rooms.items():
        room_label = "Theory" if room.room_type == "T" else "Laboratory"
        print(f"   - {r_name:<4} [Type: {room.room_type} ({room_label})]")
