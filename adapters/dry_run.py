#!/usr/bin/env python3
"""Protocol-neutral dry-run adapter.

The dry-run adapter validates that a case can be scheduled from the IR and
records planned actions. It deliberately does not claim endpoint behavior.
"""

from __future__ import annotations

from typing import Any

from .base import AdapterResult


class DryRunAdapter:
    name = "dry-run"

    def __init__(self, profile: dict[str, Any] | None = None) -> None:
        self.profile = profile or {}
        self.events_seen: list[str] = []

    def prepare(self, case: dict[str, Any]) -> AdapterResult:
        self.events_seen = []
        unsupported = unsupported_requirements(case.get("setup", {}), self.profile)
        if unsupported:
            return AdapterResult(
                status="error",
                error="unsupported requirements: " + ", ".join(unsupported),
                observation={
                    "phase": "prepare",
                    "unsupported_requirements": unsupported,
                },
            )
        return AdapterResult(
            status="ok",
            observation={
                "phase": "prepare",
                "protocol": case.get("setup", {}).get("protocol", "unknown"),
                "requirements": case.get("setup", {}).get("requirements", {}),
                "simulated": True,
            },
        )

    def execute_event(self, case: dict[str, Any], event: dict[str, Any]) -> AdapterResult:
        self.events_seen.append(str(event.get("event_id")))
        mutation = event.get("mutation")
        return AdapterResult(
            status="ok",
            observation={
                "phase": "event",
                "event_id": event.get("event_id"),
                "connection": event.get("connection"),
                "actor": event.get("actor"),
                "operation": event.get("operation"),
                "message_type": event.get("message_type"),
                "parameter_hints": event.get("parameter_hints", []),
                "features": event.get("features", []),
                "mutation": mutation,
                "mutation_target_seen": (
                    True
                    if not isinstance(mutation, dict) or not mutation.get("target_event")
                    else mutation["target_event"] in self.events_seen
                ),
                "expected_observation": event.get("expected_observation", ""),
                "simulated": True,
            },
        )

    def teardown(self, case: dict[str, Any]) -> AdapterResult:
        return AdapterResult(
            status="ok",
            observation={
                "phase": "teardown",
                "events_seen": list(self.events_seen),
                "simulated": True,
            },
        )


def unsupported_requirements(setup: dict[str, Any], profile: dict[str, Any]) -> list[str]:
    capabilities = set(profile.get("capabilities", []))
    unsupported: list[str] = []
    requirements = setup.get("requirements", {}) or {}
    requirement_map = {
        "needs_network_scheduler": "network_scheduler",
        "needs_fault_injection": "fault_injection",
        "needs_multi_connection": "multi_connection",
        "needs_replay_buffer": "replay_buffer",
        "needs_timer_control": "timer_control",
    }
    for requirement, capability in requirement_map.items():
        if requirements.get(requirement) and capability not in capabilities:
            unsupported.append(requirement)
    return unsupported

