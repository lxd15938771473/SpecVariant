"""Deliberately flawed demo implementation used by the total pipeline test."""


def accepts_limit(value: int) -> bool:
    # Bug: the protocol maximum is 10, but 11 is accepted.
    return 0 <= value <= 11


def process_token(seen: set[str], token: str) -> bool:
    # Correct behavior for the second fixture requirement.
    if token in seen:
        return False
    seen.add(token)
    return True

