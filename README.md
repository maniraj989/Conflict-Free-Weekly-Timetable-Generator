# Conflict-Free Weekly Timetable Generator

A Python-based scheduling utility designed to generate optimal, conflict-free weekly timetables for academic institutions, schools, and organizations.

---

## 📌 Problem Statement

Creating a weekly timetable manually is time-consuming and error-prone. Common challenges include:
- **Teacher/Instructor overlaps**: Multiple classes assigned to the same instructor at the same time.
- **Room/Venue clashes**: Multiple classes competing for the same classroom or lab.
- **Batch/Student conflicts**: The same cohort of students scheduled for two different subjects simultaneously.
- **Resource constraints**: Specific lab requirements or fixed capacity limits.

Manual scheduling often leads to last-minute adjustments, double-bookings, and sub-optimal distribution of workload across the week.

---

## 🎯 Objectives

- **Automate Timetable Generation**: Provide a fast, algorithmic approach to building weekly schedules.
- **Ensure Zero Conflicts**: Strictly satisfy hard constraints (no double-booked teachers, rooms, or student groups).
- **Zero External Dependencies**: Build using standard Python libraries to ensure maximum portability and ease of setup.
- **Extensible & Maintainable**: Structure code clearly so constraint-solving rules and algorithms can be enhanced iteratively.

---

## 🚀 Planned Features

- **Data Models**: Clean abstractions for Teachers, Courses/Subjects, Classrooms, and Time Slots.
- **Constraint Validation**:
  - Hard constraints: Teacher availability, Room exclusivity, Student group availability.
  - Soft constraints: Balanced daily subject loads and minimized teacher idle gaps.
- **Conflict-Free Scheduling Engine**: Backtracking search or constraint satisfaction logic to assign slots without overlaps.
- **Formatted Timetable Export & Display**: Clean console display (and optional CSV/text output) for student and instructor views.
- **Custom Availability Input**: Support for pre-defined teacher preferences and unavailable slots.

---

## 🧱 Core Data Models

Implemented in [`models.py`](models.py) using standard library `dataclasses`:

- **`Subject`**: Stores subject name, assigned faculty reference, required `periods_per_week`, and an `is_lab` boolean flag indicating venue needs.
- **`ClassGroup`**: Represents a student cohort/section and maintains its list of enrolled `Subject` objects.
- **`Faculty`**: Represents an instructor with slot-based availability tracking (`(day, period)` set) and lookup helpers.
- **`Room`**: Represents a physical classroom or laboratory, validated with venue type (`'T'` for Theory, `'L'` for Lab), and slot availability tracking.
- **`Assignment`**: Represents a scheduled class session pairing a subject, faculty member, and room.
- **`Timetable`**: Encapsulates the 3-Dimensional timetable grid (`[class_name][day][period] -> Optional[Assignment]`) across configured days and periods.

---

## 📥 Input Specification & Format

The input parser ([`parser.py`](parser.py)) reads structured text according to the following grammar:

```text
Line 1: days periods_per_day
Line 2: n (number of subject entries)
Next n lines: class_name subject_name faculty_name periods_per_week is_lab
Next line: m (number of rooms)
Next m lines: room_name room_type
```

### Field Definitions:
- **`days`**: Number of working days (automatically mapped to labels `D1`, `D2`, etc.).
- **`periods_per_day`**: Number of periods/slots per day.
- **`class_name`**: Name/identifier of the student class cohort (e.g. `X`, `Y`).
- **`subject_name`**: Name of the subject/course (e.g. `TH`, `LAB`).
- **`faculty_name`**: Name of the instructor. If the same faculty name appears across different classes, they are linked to the same shared `Faculty` entity.
- **`periods_per_week`**: Required weekly periods for this subject (> 0).
- **`is_lab`**: Flag indicating venue requirement (`0` for Theory classroom, `1` for Laboratory).
- **`room_name`**: Identifier of the venue (e.g. `R1`, `L1`).
- **`room_type`**: Venue classification (`T` for Theory classroom, `L` for Laboratory).

### Sample Input:
```text
2 3
4
X TH F1 2 0
X LAB F2 2 1
Y TH F1 2 0
Y LAB F3 2 1
3
R1 T
R2 T
L1 L
```

---

## 🛡️ Conflict Checker & Constraints

Implemented in [`validator.py`](validator.py), the conflict checker evaluates candidate placements without mutating the timetable:

### Enforced Constraints:
1. **Grid Bounds Validity**: Target day and period must exist within the configured timetable dimensions.
2. **Class Slot Exclusivity**: The target class slot must be unoccupied.
3. **Room Type Compatibility**:
   - Theory subjects (`is_lab=False`) must be scheduled in Theory rooms (`room_type='T'`).
   - Lab subjects (`is_lab=True`) must be scheduled in Laboratory rooms (`room_type='L'`).
4. **Room Availability**: The room must be available during the requested `(day, period)`.
5. **No Room Clashing**: The room cannot be assigned to another class during the same period.
6. **Faculty Availability**: The teacher must be available during the requested `(day, period)`.
7. **No Teacher Clashing**: The teacher cannot be assigned to another class during the same period (essential for shared faculty across batches).
8. **Consecutive Lab Blocks**: Two-period lab sessions (`can_place_lab_block`) must be scheduled back-to-back on the same day in the same lab room with the same faculty.

### Core Validation Functions:
- **`can_place_subject(timetable, class_name, day, period, subject, room, faculty=None) -> (bool, str)`**: Validates a single period placement.
- **`can_place_lab_block(timetable, class_name, day, start_period, subject, room, faculty=None) -> (bool, str)`**: Validates a 2-consecutive-period lab block.

---

---

## 🔍 Backtracking Search with Forward Checking & MRV

Implemented in [`search.py`](search.py) and integrated into [`scheduler.py`](scheduler.py), the scheduling engine employs a Constraint Satisfaction Problem (CSP) search engine:

### Core Concepts:
1. **Discrete Units of Work (`SchedulingUnit`)**:
   - **Theory**: Represented as 1-period atomic units.
   - **Laboratory**: Represented as 2-consecutive-period atomic blocks.
2. **Minimum Remaining Values (MRV) Heuristic**:
   - At each decision point, dynamically selects the unscheduled requirement with the fewest valid candidate placements.
   - Constrained subjects (and 2-period lab blocks) are prioritized first to fail fast and drastically shrink the search tree.
3. **Forward Checking**:
   - After each tentative assignment, inspects all remaining unscheduled units.
   - If any remaining unit loses all valid placement options (domain size becomes 0), the search rejects the tentative assignment and backtracks immediately without descending further.
4. **Reversible Assignments**:
   - Tentative placements are applied strictly using the non-mutating validators `can_place_subject` and `can_place_lab_block`.
   - On backtrack, only the assignments belonging to the active search branch are reverted (both consecutive slots for labs are undone together).
5. **Configurable Search Node Limits**:
   - Guarded by a `max_nodes` parameter (default: 5000) to protect runtime against exponential combinatorial blowups.
   - If the limit is exhausted, returns `LIMIT_REACHED` with diagnostic information rather than erroneously claiming the input is infeasible.
6. **Explicit Search Outcomes**:
   - `SOLVED`: An optimal, completely conflict-free timetable was found.
   - `INFEASIBLE`: The problem was thoroughly explored and proven impossible under the given constraints.
   - `LIMIT_REACHED`: Search terminated due to budget limits.

---

## 💻 Prerequisites & Requirements

- **Python**: Version 3.8 or higher (tested with Python 3.12).
- **Dependencies**: Standard library only (no `pip install` required).

---

## 🛠️ Instructions for Running the Application

1. **Run Conflict-Free Scheduler with Backtracking**:
   ```bash
   python main.py --schedule
   ```

2. **Run All Unit Test Suites (10 Validator Tests + 16 Scheduler Tests)**:
   ```bash
   python main.py --test
   # or run individually:
   python validator.py
   python scheduler.py
   ```

3. **Run Input Parser with Sample Data**:
   ```bash
   python main.py --sample
   ```

4. **Run with Custom Input File**:
   ```bash
   python main.py input.txt
   # or with scheduler:
   python main.py --schedule input.txt
   ```



