#!/usr/bin/env python3
"""TLS 1.3 adapter backed by OpenSSL through Python's ssl module.

This adapter is intentionally narrow: it provides a real local TLS 1.3
client/server handshake using the OpenSSL library available to Python. More
precise packet mutation and 0-RTT execution should later move to an OpenSSL CLI
or C harness backend.
"""

from __future__ import annotations

import datetime as dt
import socket
import ssl
import tempfile
import threading
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from .base import AdapterResult


class OpenSSLTLS13Adapter:
    name = "openssl-tls13"

    def __init__(self, profile: dict[str, Any] | None = None) -> None:
        self.profile = profile or {}
        self.tempdir: tempfile.TemporaryDirectory[str] | None = None
        self.cert_path: Path | None = None
        self.key_path: Path | None = None
        self.server_thread: threading.Thread | None = None
        self.server_ready = threading.Event()
        self.server_done = threading.Event()
        self.server_error: str | None = None
        self.server_observation: dict[str, Any] = {}
        self.port: int | None = None

    def prepare(self, case: dict[str, Any]) -> AdapterResult:
        if not ssl.HAS_TLSv1_3:
            return AdapterResult(
                status="unsupported",
                error="Python ssl backend does not expose TLS 1.3 support",
                observation={"phase": "prepare", "backend": ssl.OPENSSL_VERSION},
            )

        unsupported = unsupported_requirements(case.get("setup", {}), self.profile)
        unsupported.extend(unsupported_events(case.get("events", []) or []))
        if unsupported:
            return AdapterResult(
                status="unsupported",
                error="unsupported by current OpenSSL TLS 1.3 backend: " + ", ".join(unsupported),
                observation={
                    "phase": "prepare",
                    "backend": ssl.OPENSSL_VERSION,
                    "backend_kind": "python-ssl-openssl",
                    "unsupported": unsupported,
                },
            )

        self.tempdir = tempfile.TemporaryDirectory(prefix="speclitmus-openssl-")
        self.cert_path = Path(self.tempdir.name) / "server.crt"
        self.key_path = Path(self.tempdir.name) / "server.key"
        write_self_signed_cert(self.cert_path, self.key_path)

        self.server_ready.clear()
        self.server_done.clear()
        self.server_error = None
        self.server_observation = {}
        self.server_thread = threading.Thread(target=self._serve_once, daemon=True)
        self.server_thread.start()
        if not self.server_ready.wait(timeout=5):
            return AdapterResult(
                status="error",
                error="TLS server did not become ready",
                observation={"phase": "prepare", "backend": ssl.OPENSSL_VERSION},
            )

        return AdapterResult(
            status="ok",
            observation={
                "phase": "prepare",
                "backend": ssl.OPENSSL_VERSION,
                "backend_kind": "python-ssl-openssl",
                "protocol": "TLS 1.3",
                "server": "127.0.0.1",
                "port": self.port,
            },
        )

    def execute_event(self, case: dict[str, Any], event: dict[str, Any]) -> AdapterResult:
        message_type = str(event.get("message_type", ""))
        mutation = event.get("mutation")
        if message_type.casefold() in {"0-rtt", "earlydata", "early_data"}:
            return AdapterResult(
                status="unsupported",
                error=(
                    "0-RTT/early-data execution requires OpenSSL early-data APIs "
                    "or s_client/s_server; Python ssl does not expose them here"
                ),
                observation={
                    "phase": "event",
                    "event_id": event.get("event_id"),
                    "message_type": message_type,
                    "mutation": mutation,
                    "backend": ssl.OPENSSL_VERSION,
                },
            )
        if isinstance(mutation, dict):
            return AdapterResult(
                status="unsupported",
                error="packet-level mutation is not supported by the Python ssl backend",
                observation={
                    "phase": "event",
                    "event_id": event.get("event_id"),
                    "message_type": message_type,
                    "mutation": mutation,
                    "backend": ssl.OPENSSL_VERSION,
                },
            )
        if event.get("operation") != "send":
            return AdapterResult(
                status="unsupported",
                error=f"unsupported operation: {event.get('operation')}",
                observation={"phase": "event", "event_id": event.get("event_id")},
            )

        if "clienthello" in message_type.casefold() or message_type == "ProtocolMessage":
            return self._run_handshake_event(event)

        return AdapterResult(
            status="unsupported",
            error=f"unsupported TLS message type for this adapter: {message_type}",
            observation={
                "phase": "event",
                "event_id": event.get("event_id"),
                "message_type": message_type,
            },
        )

    def teardown(self, case: dict[str, Any]) -> AdapterResult:
        if self.server_thread:
            self.server_done.wait(timeout=2)
            self.server_thread.join(timeout=2)
        if self.tempdir:
            self.tempdir.cleanup()
            self.tempdir = None
        return AdapterResult(
            status="ok",
            observation={
                "phase": "teardown",
                "server_observation": self.server_observation,
                "server_error": self.server_error,
            },
        )

    def _run_handshake_event(self, event: dict[str, Any]) -> AdapterResult:
        assert self.port is not None
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.maximum_version = ssl.TLSVersion.TLSv1_3
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=5) as raw:
                with context.wrap_socket(raw, server_hostname="localhost") as tls:
                    tls.sendall(b"speclitmus-handshake-probe\n")
                    try:
                        response = tls.recv(4096)
                    except socket.timeout:
                        response = b""
                    observation = {
                        "phase": "event",
                        "event_id": event.get("event_id"),
                        "connection": event.get("connection"),
                        "actor": event.get("actor"),
                        "operation": event.get("operation"),
                        "message_type": event.get("message_type"),
                        "parameter_hints": event.get("parameter_hints", []),
                        "tls_version": tls.version(),
                        "cipher": tls.cipher(),
                        "server_response": response.decode("utf-8", errors="replace"),
                        "backend": ssl.OPENSSL_VERSION,
                    }
            return AdapterResult(status="ok", observation=observation)
        except Exception as exc:  # pragma: no cover - exercised by integration run
            return AdapterResult(
                status="error",
                error=str(exc),
                observation={
                    "phase": "event",
                    "event_id": event.get("event_id"),
                    "message_type": event.get("message_type"),
                    "backend": ssl.OPENSSL_VERSION,
                },
            )

    def _serve_once(self) -> None:
        assert self.cert_path is not None
        assert self.key_path is not None
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.maximum_version = ssl.TLSVersion.TLSv1_3
        context.load_cert_chain(str(self.cert_path), str(self.key_path))

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                listener.bind(("127.0.0.1", 0))
                listener.listen(1)
                listener.settimeout(8)
                self.port = int(listener.getsockname()[1])
                self.server_ready.set()
                raw, address = listener.accept()
                with raw:
                    with context.wrap_socket(raw, server_side=True) as tls:
                        tls.settimeout(5)
                        data = tls.recv(4096)
                        tls.sendall(b"speclitmus-ok\n")
                        self.server_observation = {
                            "client": f"{address[0]}:{address[1]}",
                            "tls_version": tls.version(),
                            "cipher": tls.cipher(),
                            "bytes_received": len(data),
                            "backend": ssl.OPENSSL_VERSION,
                        }
        except Exception as exc:  # pragma: no cover - exercised by integration run
            self.server_error = str(exc)
        finally:
            self.server_done.set()


def write_self_signed_cert(cert_path: Path, key_path: Path) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "SpecLitmus"),
            x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
        ]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(dt.datetime.utcnow() - dt.timedelta(minutes=1))
        .not_valid_after(dt.datetime.utcnow() + dt.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .sign(key, hashes.SHA256())
    )

    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def unsupported_requirements(setup: dict[str, Any], profile: dict[str, Any]) -> list[str]:
    capabilities = set(profile.get("capabilities", []))
    requirements = setup.get("requirements", {}) or {}
    requirement_map = {
        "needs_network_scheduler": "network_scheduler",
        "needs_fault_injection": "fault_injection",
        "needs_multi_connection": "multi_connection",
        "needs_replay_buffer": "replay_buffer",
        "needs_timer_control": "timer_control",
    }
    unsupported: list[str] = []
    for requirement, capability in requirement_map.items():
        if requirements.get(requirement) and capability not in capabilities:
            unsupported.append(requirement)
    return unsupported


def unsupported_events(events: list[dict[str, Any]]) -> list[str]:
    unsupported: list[str] = []
    for event in events:
        message_type = str(event.get("message_type", "")).casefold()
        mutation = event.get("mutation")
        if message_type in {"0-rtt", "earlydata", "early_data"}:
            unsupported.append(f"{event.get('event_id')}:early_data")
        if isinstance(mutation, dict):
            unsupported.append(f"{event.get('event_id')}:mutation:{mutation.get('type')}")
    return unsupported
