from __future__ import annotations

import json
import sys

from limit_impl import accepts_limit


def main() -> int:
    passed = accepts_limit(10) is True
    print(json.dumps({"limit_10_accepted": passed}, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
