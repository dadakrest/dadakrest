"""Build every PDF in docs/pdf/ from the data center's own data.

    python3 tools/pdf/build.py              # all of them
    python3 tools/pdf/build.py fee_schedule # just one

Each module in DOCUMENTS exposes `build() -> Path`. All but the employment
application need only Python and Chromium; that one makes a fillable form and
needs `pip install reportlab`, and is skipped with a message when it is absent.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

DOCUMENTS = (
    "ai_data_center",
    "fee_schedule",
    "ai_tools_and_databases",
    "license_agreement",
    "llc_operating_agreement",
    "employment_application",
)


def main(argv: list[str]) -> int:
    chosen = argv or list(DOCUMENTS)
    unknown = sorted(set(chosen) - set(DOCUMENTS))
    if unknown:
        print(f"unknown document(s): {', '.join(unknown)}; choose from {', '.join(DOCUMENTS)}")
        return 2

    failed = 0
    for name in chosen:
        try:
            module = importlib.import_module(name)
            path = module.build()
        except ModuleNotFoundError as error:
            if error.name == "reportlab":
                print(f"skipped {name}: needs reportlab (pip install reportlab)")
                continue
            raise
        except Exception as error:  # report every document, not just the first failure
            failed += 1
            print(f"FAILED {name}: {error}")
            continue
        print(f"built {path.relative_to(path.parents[2])}  ({path.stat().st_size // 1024} KB)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
