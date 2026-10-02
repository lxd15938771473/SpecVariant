# `close()` accepts out-of-range error codes and panics in `send()`

- Type: implementation robustness / API validation issue
- Note: this is a real standards-related bug, but the failure happens before serialization, so this report does not claim that an invalid `CONNECTION_CLOSE` frame was observed on wire.

## Standard Requirement

RFC 9000 Section 19.19: [CONNECTION_CLOSE Frames](https://www.rfc-editor.org/rfc/rfc9000.html#section-19.19)

```text
An endpoint sends a CONNECTION_CLOSE frame (type=0x1c or 0x1d) ...

Error Code:  A variable-length integer that indicates the reason for
   closing this connection.  A CONNECTION_CLOSE frame of type 0x1c
   uses codes from the space defined in Section 20.1.  A
   CONNECTION_CLOSE frame of type 0x1d uses codes defined by the
   application protocol; see Section 20.2.
```

RFC 9000 Section 20: [Error Codes](https://www.rfc-editor.org/rfc/rfc9000.html#section-20)

```text
QUIC transport error codes and application error codes are 62-bit
unsigned integers.
```

RFC 9000 Section 22.5: [QUIC Transport Error Codes Registry](https://www.rfc-editor.org/rfc/rfc9000.html#section-22.5)

```text
The "QUIC Transport Error Codes" registry governs a 62-bit space.
```

Interpretation: the `Error Code` placed in `CONNECTION_CLOSE` must stay within the QUIC 62-bit varint space.

## Relevant Code

`quiche/src/lib.rs:7553-7577`

```rust
pub fn close(&mut self, app: bool, err: u64, reason: &[u8]) -> Result<()> {
    ...
    self.local_error = Some(ConnectionError {
        is_app: app,
        error_code: err,
        reason: reason.to_vec(),
    });
}
```

`quiche/include/quiche.h:481-483`

```c
int quiche_conn_close(quiche_conn *conn, bool app, uint64_t err,
                      const uint8_t *reason, size_t reason_len);
```

`quiche/src/lib.rs:4973-4998`, `quiche/src/lib.rs:1886-1893`, `quiche/src/frame.rs:777-793`, `octets/src/lib.rs:826-836`

```rust
let frame = frame::Frame::ConnectionClose {
    error_code: conn_err.error_code,
    frame_type: 0,
    reason: conn_err.reason.clone(),
};

if $frame.wire_len() <= $left {
    $frame.to_bytes(&mut $out)?;
}

octets::varint_len(*error_code)

pub const fn varint_len(v: u64) -> usize {
    ...
    } else {
        unreachable!()
    }
}
```

The code path is direct: `close()` accepts any `u64`, and `send()` computes `wire_len()` before serialization; values above 62 bits hit `unreachable!()` inside `varint_len()`.

## Runtime Evidence

Run from the round output root:

```bash
cargo run --quiet --manifest-path runtime/0009-transport-error-code-varint-bound/Cargo.toml
```

Observed output:

```text
case=transport-valid-max app=false err=0x3fffffffffffffff close_result=Ok(())
case=transport-valid-max send=ok len=49 from=127.0.0.1:1234 to=127.0.0.1:4321
case=transport-out-of-range app=false err=0x4000000000000000 close_result=Ok(())
case=transport-out-of-range send=panic
case=application-valid-max app=true err=0x3fffffffffffffff close_result=Ok(())
case=application-valid-max send=ok len=48 from=127.0.0.1:1234 to=127.0.0.1:4321
case=application-out-of-range app=true err=0x4000000000000000 close_result=Ok(())
case=application-out-of-range send=panic
```

With `RUST_BACKTRACE=1`, the panic chain is:

```text
octets::varint_len
Frame::wire_len
Connection::send_single
Connection::send_on_path
Connection::send
```

## Impact And Fix Direction

- Impact: a Rust or C caller can pass an out-of-range error code through a public API and crash during `send()`.
- Fix: validate `err <= 0x3fff_ffff_ffff_ffff` in `close()` / `quiche_conn_close()` and return a regular error instead of panicking.
