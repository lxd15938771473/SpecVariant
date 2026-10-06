# disable_active_migration not enforced

## Merged Redundant Reports

The following report had the same implementation cause and the same required fix, so it has been merged into this report and the redundant file was removed:

- `1001-1500/0229-disable_active_migration_ignored.md`

The shared cause is that `disable_active_migration` is parsed but not enforced in sender-side probing and migration paths. The shared fix is to add checks in `probe_path()`, `migrate_source()`, and `migrate()` when the peer set `disable_active_migration`, unless the preferred-address exception actually applies.

## Standard Requirement

- RFC 9000 Section 9: [Connection Migration](https://www.rfc-editor.org/rfc/rfc9000.html#section-9)
- RFC 9000 Section 9.6: [Server's Preferred Address](https://www.rfc-editor.org/rfc/rfc9000.html#section-9.6)
- RFC 9000 Section 18.2: [Transport Parameter Definitions](https://www.rfc-editor.org/rfc/rfc9000.html#section-18.2)

```text
If the peer sent the disable_active_migration transport parameter, an
endpoint also MUST NOT send packets (including probing packets; see
Section 9.1) from a different local address to the address the peer
used during the handshake, unless the endpoint has acted on a
preferred_address transport parameter from the peer.
```

RFC 9000 also says this exception applies only after the client has acted on `preferred_address`. `quiche` does not implement `preferred_address` (`quiche/src/transport_params.rs:348`, `quiche/src/transport_params.rs:528`), so the exception is unavailable.

## Relevant Source Code

- `quiche/src/transport_params.rs:339-340`, `quiche/src/transport_params.rs:524-528`: quiche parses and encodes `disable_active_migration`, but `preferred_address` is still `TODO`.
- `quiche/src/lib.rs:7188-7200`: `probe_path()` creates or reuses a path and calls `request_validation()` without checking `peer_transport_params.disable_active_migration`.
- `quiche/src/lib.rs:7230-7289`: `migrate()` switches to the new path without checking `peer_transport_params.disable_active_migration`.
- `quiche/src/lib.rs:9096-9113`, `quiche/src/path.rs:323-339`, `quiche/src/path.rs:880-906`: probing paths and active paths are eligible for sending, so the unchecked paths can actually emit packets.

## Runtime Evidence

- Date: `2026-08-12`
- Command: `cargo test -p tokio-quiche --test main disable_active_migration_runtime -- --nocapture`

```text
probe_path flight_paths=[(127.0.0.1:33457, 127.0.0.1:50505), (127.0.0.1:33456, 127.0.0.1:50505)]
migrate_source flight_paths=[(127.0.0.1:34457, 127.0.0.1:50504)]
```

The test first confirmed that the server advertised `disable_active_migration=true`, then invoked `probe_path()` and `migrate_source()`. `probe_path()` still emitted a packet from the new local address `127.0.0.1:33457` to the server's handshake address `127.0.0.1:50505`; `migrate_source()` emitted packets from `127.0.0.1:34457` to `127.0.0.1:50504`. The `probe_path()` packet alone is sufficient to violate RFC 9000 because probing packets are explicitly included.

## Conclusion

quiche records `disable_active_migration` but does not enforce it on either probing or active migration send paths. This is a real RFC 9000 compliance issue.
