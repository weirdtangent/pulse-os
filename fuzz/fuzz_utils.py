import sys

import atheris

with atheris.instrument_imports():
    from pulse.utils import (
        chunk_bytes,
        parse_bool,
        parse_float,
        parse_int,
        sanitize_hostname_for_entity_id,
        split_csv,
    )


def TestOneInput(data: bytes) -> None:
    """Fuzz utility parsing functions with arbitrary input."""
    value = data.decode("utf-8", errors="ignore")

    # Test string sanitization
    sanitize_hostname_for_entity_id(value)

    # Test parsers with default fallbacks (should never raise)
    parse_bool(value)
    parse_int(value, default=0)
    parse_float(value, default=0.0)
    split_csv(value)

    # Test chunk_bytes with the raw data
    if len(data) > 0:
        try:
            # Use a size derived from input to vary chunk sizes
            size = (data[0] % 64) + 1  # 1-64 byte chunks
            list(chunk_bytes(data, size))
        except ValueError:
            pass  # Expected for size <= 0


def main() -> None:
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
