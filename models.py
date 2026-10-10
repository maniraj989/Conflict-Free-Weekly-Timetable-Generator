"""Data models for the Conflict-Free Weekly Timetable Generator.

This module defines the foundational entities used in timetable generation:
- Assignment: Represents an individual scheduled class (subject, teacher, room).
- Subject: Represents an academic course with weekly frequency and lab indicator.
- ClassGroup: Represents a student cohort/section and their assigned subjects.
- Faculty: Represents an instructor and their available time slots.
- Room: Represents a physical venue (Theory classroom or Lab) and its availability.
- Timetable: Represents the complete 3-D schedule organized by class, day, and period.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple


DEFAULT_FACULTY_REGISTRY: Dict[str, str] = {
    "F1": "Dr.Deena Rose",
    "F2": "Dr.V.Mani",
    "F3": "Dr.Jegan",
    "F4": "Dr.S.Sakthviel",
}

SUBJECT_CATALOG: Dict[str, bool] = {
    "APP": False,       # Theory
    "COA": False,       # Theory
    "DSA": False,       # Theory
    "DSA LAB": True,    # Laboratory
    "OS": False,        # Theory
    "OS LAB": True,     # Laboratory
}


def get_faculty_display_name(faculty_id: str, registry: Optional[Dict[str, str]] = None) -> str:
    """Return the display name for a faculty ID using the registry (or default)."""
    reg = registry if registry is not None else DEFAULT_FACULTY_REGISTRY
    return reg.get(faculty_id, faculty_id)


def format_faculty_label(faculty_id: str, registry: Optional[Dict[str, str]] = None) -> str:
    """Format faculty with display name and ID, e.g. 'Dr.Deena Rose (F1)' or 'F1'."""
    display = get_faculty_display_name(faculty_id, registry)
    if display and display != faculty_id:
        return f"{display} ({faculty_id})"
    return faculty_id


@dataclass
class Assignment:
    """Represents a scheduled class session.

    Attributes:
        subject: The name of the subject being taught.
        faculty: The internal ID of the instructor teaching the class (e.g. 'F1').
        room: The name of the room or lab allocated for the session.
        faculty_display_name: Full display name of the instructor (e.g. 'Dr.Deena Rose').
    """

    subject: str
    faculty: str
    room: str
    faculty_display_name: str = ""

    def __post_init__(self) -> None:
        if not self.faculty_display_name:
            self.faculty_display_name = get_faculty_display_name(self.faculty)

    def __str__(self) -> str:
        fac_lbl = format_faculty_label(self.faculty)
        return f"{self.subject} [Faculty: {fac_lbl}, Room: {self.room}]"


@dataclass
class Subject:
    """Represents a course/subject to be scheduled.

    Attributes:
        name: Name of the subject (e.g., 'DSA', 'DSA LAB', 'OS').
        faculty: Internal faculty ID of assigned instructor (e.g., 'F1').
        periods_per_week: Total number of weekly periods needed for this subject.
        is_lab: Boolean indicating whether this subject requires a laboratory venue.
        faculty_display_name: Full display name of the assigned instructor.
    """

    name: str
    faculty: str
    periods_per_week: int
    is_lab: bool = False
    faculty_display_name: str = ""

    def __post_init__(self) -> None:
        if not self.faculty_display_name:
            self.faculty_display_name = get_faculty_display_name(self.faculty)


@dataclass
class ClassGroup:
    """Represents a student cohort, batch, or section.

    Attributes:
        name: Name or identifier of the class (e.g., 'CS-Year2-SecA').
        subjects: List of subjects that this class group is required to take.
    """

    name: str
    subjects: List[Subject] = field(default_factory=list)

    def add_subject(self, subject: Subject) -> None:
        """Add a subject to this class group's curriculum."""
        self.subjects.append(subject)


@dataclass
class Faculty:
    """Represents a teacher or instructor.

    Attributes:
        name: Internal ID of the faculty member (e.g. 'F1').
        availability: Set of (day, period) tuples representing available time slots.
                      If empty, the faculty member is considered available for all slots.
        display_name: Full display name of the instructor (e.g. 'Dr.Deena Rose').
    """

    name: str
    availability: Set[Tuple[str, int]] = field(default_factory=set)
    display_name: str = ""

    def __post_init__(self) -> None:
        if not self.display_name:
            self.display_name = get_faculty_display_name(self.name)

    def is_available(self, day: str, period: int) -> bool:
        """Check if the faculty member is available at a given day and period.

        Returns True if availability is unconstrained (empty) or slot is present.
        """
        if not self.availability:
            return True
        return (day, period) in self.availability

    def mark_available(self, day: str, period: int) -> None:
        """Register a specific (day, period) slot as available."""
        self.availability.add((day, period))


@dataclass
class Room:
    """Represents a physical venue such as a classroom or lab.

    Attributes:
        name: Name or room number (e.g., 'Room 101', 'Computer Lab 2').
        room_type: Venue category ('T' for Theory, 'L' for Lab).
        availability: Set of (day, period) tuples representing available time slots.
                      If empty, the room is considered available for all slots.
    """

    name: str
    room_type: str = "T"  # 'T' for Theory classroom, 'L' for Laboratory
    availability: Set[Tuple[str, int]] = field(default_factory=set)

    def __post_init__(self) -> None:
        normalized_type = self.room_type.upper()
        if normalized_type not in ("T", "L"):
            raise ValueError(f"Invalid room_type '{self.room_type}'. Must be 'T' (Theory) or 'L' (Lab).")
        self.room_type = normalized_type

    def is_available(self, day: str, period: int) -> bool:
        """Check if the room is available at a given day and period.

        Returns True if availability is unconstrained (empty) or slot is present.
        """
        if not self.availability:
            return True
        return (day, period) in self.availability

    def mark_available(self, day: str, period: int) -> None:
        """Register a specific (day, period) slot as available."""
        self.availability.add((day, period))


@dataclass
class Timetable:
    """Represents the complete weekly timetable schedule across all classes.

    Uses a 3-Dimensional nested dictionary representation:
        grid[class_name][day][period] -> Optional[Assignment]

    Attributes:
        days: List of active scheduling days (e.g., Monday through Friday).
        periods_per_day: Number of periods/slots scheduled per day.
        grid: 3-D structure storing scheduled assignments.
    """

    days: List[str] = field(
        default_factory=lambda: ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    )
    periods_per_day: int = 6
    grid: Dict[str, Dict[str, Dict[int, Optional[Assignment]]]] = field(
        default_factory=dict
    )

    def initialize_class(self, class_name: str) -> None:
        """Initialize an empty schedule grid for a given class group."""
        if class_name not in self.grid:
            self.grid[class_name] = {
                day: {period: None for period in range(1, self.periods_per_day + 1)}
                for day in self.days
            }

    def assign(
        self, class_name: str, day: str, period: int, assignment: Assignment
    ) -> None:
        """Schedule an assignment for a class at a specific day and period."""
        if class_name not in self.grid:
            self.initialize_class(class_name)
        if day not in self.grid[class_name]:
            raise KeyError(f"Day '{day}' is not part of this timetable.")
        if period not in self.grid[class_name][day]:
            raise KeyError(f"Period '{period}' is outside valid range (1-{self.periods_per_day}).")

        self.grid[class_name][day][period] = assignment

    def get_assignment(
        self, class_name: str, day: str, period: int
    ) -> Optional[Assignment]:
        """Retrieve the scheduled assignment for a class at a given day and period."""
        return self.grid.get(class_name, {}).get(day, {}).get(period)
