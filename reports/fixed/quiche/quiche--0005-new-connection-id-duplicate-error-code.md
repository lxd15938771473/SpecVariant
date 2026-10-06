# NEW_CONNECTION_ID duplicate conflict uses FRAME_ENCODING_ERROR

## Standard

RFC 9000 gives this exact condition its own rule:

```text
If an endpoint receives a NEW_CONNECTION_ID frame that repeats a
previously issued connection ID with a different Stateless Reset
Token field value or a different Sequence Number field value, or if a
sequence number is used for different connection IDs, the endpoint
MAY treat that receipt as a connection error of type
PROTOCOL_VIOLATION.
```

The next paragraph assigns `FRAME_ENCODING_ERROR` to a different case:

```text
Receiving a value in the Retire Prior To field that is greater than
that in the Sequence Number field MUST be treated as a connection
error of type FRAME_ENCODING_ERROR.
```

The error-code rules in Section 11 and Section 20 point the same way:

```text
The most appropriate error code SHOULD be included in the frame that
signals the error.
```

```text
FRAME_ENCODING_ERROR (0x07): An endpoint received a frame that was
badly formatted.

PROTOCOL_VIOLATION (0x0a): An endpoint detected an error with
protocol compliance that was not covered by more specific error codes.
```

This duplicate-conflict case is not a malformed frame. If the endpoint chooses to close, `PROTOCOL_VIOLATION` is the RFC-matched code.

## Code

`quiche/src/cid.rs:458-468`

```rust
// ... MAY treat that receipt as a connection error of type
// PROTOCOL_VIOLATION.
if let Some(e) = self.dcids.iter().find(|e| e.cid == cid || e.seq == seq) {
    if e.cid != cid || e.seq != seq || e.reset_token != Some(reset_token) {
        return Err(Error::InvalidFrame);
    }
    return Ok(());
}
```

`quiche/src/error.rs:185-200`

```rust
Error::InvalidFrame => WireErrorCode::FrameEncodingError as u64,
_ => WireErrorCode::ProtocolViolation as u64,
```

`quiche/src/lib.rs:8709-8748` passes the `new_dcid()` error upward, and `quiche/src/lib.rs:2885` closes the connection with `self.close(false, e.to_wire(), b"")`.

So the duplicate-conflict branch is detected correctly, but it is reported on the wire as `0x07`.

## Runtime Evidence

Actual execution was performed on 2026-08-12 with a temporary focused unit test, then the test was removed after verification.

The temporary focused unit test did the following:

1. complete the handshake
2. advertise a CID with `seq_num = 1`
3. inject `NEW_CONNECTION_ID { seq_num = 1, conn_id = same CID, reset_token = different }`
4. inspect the server error and emitted `CONNECTION_CLOSE`

Command:

```text
cargo test -p quiche connection_id_duplicate_conflict_runtime_repro
```

Observed output:

```text
running 2 tests
test tests::connection_id_duplicate_conflict_runtime_repro::cc_algorithm_name_1___cubic__ ... ok
test tests::connection_id_duplicate_conflict_runtime_repro::cc_algorithm_name_2___bbr2_gcongestion__ ... ok

test result: ok. 2 passed; 0 failed; 0 ignored; 0 measured; 1095 filtered out
```

Assertions checked by the reproducer:

- `recv()` returned `Err(Error::InvalidFrame)`
- `local_error.error_code == 0x07`
- emitted `CONNECTION_CLOSE { error_code: 0x07 }`

## Conclusion

This is a real but narrow standards-compliance bug: `quiche` recognizes the duplicate `NEW_CONNECTION_ID` conflict, but maps it to `FRAME_ENCODING_ERROR (0x07)` instead of `PROTOCOL_VIOLATION (0x0a)`.
