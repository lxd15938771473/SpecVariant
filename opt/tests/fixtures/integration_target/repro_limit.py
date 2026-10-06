from __future__ import annotations

import json
import sys

from limit_impl import accepts_limit


def main() -> int:
    invalid_rejected = accepts_limit(11) is False
    observation = {
        "invalid_value_11": "rejected" if invalid_rejected else "accepted",
        "protocol_violation_observed": not invalid_rejected,
    }
    print(json.dumps(observation, sort_keys=True))
    return 1 if observation["protocol_violation_observed"] else 0


if __name__ == "__main__":
    sys.exit(main())
