import sys

import atheris  # type: ignore[import-not-found]
from pulse.overlay_server import OverlayHttpServer


def TestOneInput(data: bytes) -> None:
    # Fuzz the header sanitizer with arbitrary bytes decoded as latin-1 to preserve mapping.
    value = data.decode("latin-1", errors="ignore")
    dummy = object.__new__(OverlayHttpServer)
    dummy.logger = None
    OverlayHttpServer._sanitize_header_value(dummy, value)


def main() -> None:
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
