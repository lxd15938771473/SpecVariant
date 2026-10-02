# Remembered forbidden transport parameters are applied during resumption

## Standard

RFC 9000 7.4.1 says stored transport parameters are used for 0-RTT on the new connection until the handshake completes, but the client `MUST NOT` use remembered values for `ack_delay_exponent`, `max_ack_delay`, `initial_source_connection_id`, `original_destination_connection_id`, `preferred_address`, `retry_source_connection_id`, or `stateless_reset_token`. For those parameters, the client must use the new handshake values, or defaults if the server does not send them.

## Code

- `quiche/src/tls/mod.rs:1009-1040`: serializes `handshake.quic_transport_params()` into the cached session.
- `quiche/src/lib.rs:2400-2417`: `set_session()` decodes the cached raw transport parameters and immediately calls `process_peer_transport_params()`.
- `quiche/src/lib.rs:7972-8004`: applies remembered `max_ack_delay` and stores the remembered peer transport parameters.
- `quiche/src/lib.rs:8291-8295`: ACK decoding uses remembered `ack_delay_exponent`.
- `quiche/src/lib.rs:2931-2938` and `quiche/src/lib.rs:3407-3409`: connection processing uses remembered `stateless_reset_token`.
- `quiche/src/lib.rs:8086-8123`: fresh handshake transport parameters are parsed only later.

## Runtime Evidence

See `validation/remembered-transport-state-boundary-runtime/runtime-summary.md`.

Commands:

```powershell
cargo test -p quiche handshake_0rtt -- --nocapture
cargo run -p quiche --example remembered_transport_state_probe
```

Observed result:

```text
test result: ok. 7 passed; 0 failed
remembered_ack_delay_exponent=8
remembered_max_ack_delay=123
remembered_initial_source_connection_id=[hex omitted]
remembered_original_destination_connection_id=[hex omitted]
remembered_stateless_reset_token=[hex omitted]
set_session_original=ok
set_session_mutated=InvalidTransportParam
```

This shows a real session ticket carries forbidden remembered parameters, and `set_session()` decodes and applies them on the new connection.

## Decision

quiche does not just cache these parameters; it uses them during resumption before the new handshake transport parameters are available. That violates RFC 9000 7.4.1 for the prohibited parameter set, so this behavior violates the RFC 9000 transport-parameter rule.
