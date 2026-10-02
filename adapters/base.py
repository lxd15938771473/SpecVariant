#!/usr/bin/env python3
"""Protocol-neutral adapter contract for executing SpecLitmus IR cases."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class AdapterResult:
    """Result of one adapter operation."""

    status: str
    observation: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class LitmusAdapter(Protocol):
    """Minimal contract implemented by protocol-specific execution adapters."""

    name: str

    def prepare(self, case: dict[str, Any]) -> AdapterResult:
        """Prepare the endpoint, scheduler, and case-level state."""

    def execute_event(self, case: dict[str, Any], event: dict[str, Any]) -> AdapterResult:
        """Execute or schedule one normalized IR event."""

    def teardown(self, case: dict[str, Any]) -> AdapterResult:
        """Release adapter resources for this case."""

