"""Conflict-Free Weekly Timetable Generator

Application entry point for parsing, conflict validation, and greedy theory scheduling.
"""

import sys
from parser import SAMPLE_INPUT, parse_timetable_input, print_parsed_summary


def load_input_text() -> str:
    """Load timetable input from standard input, a specified file, or sample fallback."""
    # Option 1: File path provided via CLI argument
    file_args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if file_args:
        file_path = file_args[0]
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return f.read()
        except OSError as e:
            print(f"Error opening file '{file_path}': {e}", file=sys.stderr)
            sys.exit(1)

    # Option 2: Explicit sample flag or default
    if any(arg in ("--sample", "-s") for arg in sys.argv):
        print("[Info: Using built-in sample input via CLI flag]")
        return SAMPLE_INPUT

    # Option 3: Explicit stdin flag or piped stdin
    if any(arg in ("--stdin", "-i") for arg in sys.argv):
        return sys.stdin.read()

    # Fallback default: If running interactively or no stdin flag, use sample input
    return SAMPLE_INPUT


def main() -> None:
    # 1. Run Unit Test Suites (--test)
    if any(arg in ("--test", "-t", "--test-validator", "--test-scheduler") for arg in sys.argv):
        from validator import run_validator_tests
        from scheduler import run_scheduler_tests

        val_ok = run_validator_tests()
        sched_ok = run_scheduler_tests()
        if not (val_ok and sched_ok):
            sys.exit(1)
        return

    # 2. Run Complete Timetable Scheduler (--schedule)
    if any(arg in ("--schedule", "-sc") for arg in sys.argv):
        print("=" * 70)
        print("       Conflict-Free Weekly Timetable Generator")
        print("=" * 70)
        print("Step 8: Backtracking, Forward Checking & Gap Optimization")
        print("=" * 70)

        # Parse output file if specified via --output or -o
        output_file = "timetable_output.txt"
        skip_next = False
        non_flag_args = []
        for i, arg in enumerate(sys.argv[1:], start=1):
            if skip_next:
                skip_next = False
                continue
            if arg in ("--output", "-o"):
                if i + 1 < len(sys.argv):
                    output_file = sys.argv[i + 1]
                    skip_next = True
                continue
            if not arg.startswith("-"):
                non_flag_args.append(arg)

        # File argument if provided alongside --schedule, otherwise sample input
        if non_flag_args:
            file_path = non_flag_args[0]
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    input_text = f.read()
            except OSError as e:
                print(f"Error opening file '{file_path}': {e}", file=sys.stderr)
                sys.exit(1)
        else:
            print("[Info: Scheduling supplied sample input]")
            input_text = SAMPLE_INPUT

        try:
            parsed_data = parse_timetable_input(input_text)
        except ValueError as err:
            print(f"\n[PARSING ERROR]: {err}", file=sys.stderr)
            sys.exit(1)

        from scheduler import (
            print_scheduling_report,
            print_timetable_grid,
            save_timetable_output,
            schedule_timetable,
        )

        timetable, report = schedule_timetable(parsed_data)

        if timetable is not None:
            print_timetable_grid(timetable)
        else:
            print("\n[Notice: No complete timetable grid to display]")

        print_scheduling_report(report)

        print("\n" + "=" * 70)
        status_msg = (
            "Complete weekly timetable generated with zero conflicts!"
            if report.success
            else f"Notice: Search ended with status '{report.search_status}'."
        )
        print(f"Status: {status_msg}")
        print("=" * 70)

        # Save timetable and report to output file
        save_timetable_output(output_file, timetable, report)
        print(f"\n[Info: Timetable output saved to '{output_file}']")
        return

    # 3. Default Mode: Input Parsing & Summary (--sample or piped input)
    print("=" * 60)
    print("       Conflict-Free Weekly Timetable Generator")
    print("=" * 60)
    print("Step 3/4: Input Parser & Data Initialization")
    print("=" * 60)

    input_text = load_input_text()

    try:
        parsed_data = parse_timetable_input(input_text)
    except ValueError as err:
        print(f"\n[PARSING ERROR]: {err}", file=sys.stderr)
        sys.exit(1)

    print_parsed_summary(parsed_data)

    print("\n" + "=" * 60)
    print("Verification: Input parsed and structured successfully!")
    print("=" * 60)


if __name__ == "__main__":
    main()
