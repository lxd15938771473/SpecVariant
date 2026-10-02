# Client migration is reachable before handshake confirmation

## Standard Requirement

- RFC 9000 Section 9: [Connection Migration](https://www.rfc-editor.org/rfc/rfc9000.html#section-9)
- RFC 9000 Section 9.1: [Probing a New Path](https://www.rfc-editor.org/rfc/rfc9000.html#section-9.1)
- RFC 9001 Section 4.1.2: [Handshake Confirmed](https://www.rfc-editor.org/rfc/rfc9001.html#section-4.1.2)

RFC 9000 Section 9 says endpoints `MUST NOT initiate connection migration` before the handshake is confirmed. RFC 9001 Section 4.1.2 further defines client-side handshake confirmation: the client must either receive `HANDSHAKE_DONE` or receive an ACK for one of its 1-RTT packets.

RFC 9000 Section 9.1 permits path validation before migrating, but it does not relax the Section 9 precondition. A client with `handshake_confirmed == false` must not move non-probing traffic to a new address.

## Relevant Source Code

- `quiche/src/lib.rs:7230-7289`: `migrate()` does not check `handshake_confirmed`.
- `quiche/src/lib.rs:9173-9211`: `create_path_on_client()` checks only CID availability, not handshake confirmation.
- `quiche/src/lib.rs:8220-8256`: once `is_established()` is true, the scheduler can emit 1-RTT short packets.
- `quiche/src/lib.rs:8805-8815`: the client sets `handshake_confirmed = true` only when processing `HANDSHAKE_DONE`.

The implementation reduces the migration gate to CID availability instead of enforcing the required protocol state.

## Runtime Evidence

I ran the focused reproducer:

```text
cargo test pre_handshake_client_migration_reachable_with_split_new_cid_and_handshake_done -- --nocapture
```

The test observed:

```text
observed pre-handshake migration: out_size=70, client_confirmed=false, send_from=127.0.0.1:5678
```

The reproducer asserted all of the following:

1. The client still had `handshake_confirmed == false`.
2. The client switched to the new address `127.0.0.1:5678` through `new_scid()` and `migrate()`.
3. The next packet was not a probing packet; it carried `"ping"` stream data.
4. The server received that stream data on the new path.

## Reasoning

The standard forbids client-initiated migration before handshake confirmation. `quiche` does not enforce `handshake_confirmed` on the migration path, and the runtime reproducer shows that the client can migrate and send non-probing data while `handshake_confirmed == false`. This behavior violates the requirement.
