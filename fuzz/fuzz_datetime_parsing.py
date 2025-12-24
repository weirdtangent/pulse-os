import sys

import atheris

with atheris.instrument_imports():
    from pulse.datetime_utils import (
        parse_datetime,
        parse_duration_seconds,
        parse_iso_duration,
        parse_time_of_day,
        parse_time_string,
    )


def TestOneInput(data: bytes) -> None:
    """Fuzz datetime parsing functions with arbitrary input."""
    value = data.decode("utf-8", errors="ignore")

    # Test duration parsers (should not raise unhandled exceptions)
    try:
        parse_iso_duration(value)
    except ValueError:
        pass  # Expected for invalid input

    try:
        parse_duration_seconds(value)
    except ValueError:
        pass  # Expected for invalid input

    # Test time parsers
    try:
        parse_time_of_day(value)
    except ValueError:
        pass  # Expected for invalid input

    try:
        parse_time_string(value)
    except ValueError:
        pass  # Expected for invalid input

    # Test general datetime parser (returns None for invalid input)
    parse_datetime(value)


def main() -> None:
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
