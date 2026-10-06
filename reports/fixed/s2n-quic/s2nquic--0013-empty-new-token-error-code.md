# Empty NEW_TOKEN uses PROTOCOL_VIOLATION instead of FRAME_ENCODING_ERROR

s2n-quic rejects an empty `NEW_TOKEN`, but the reachable path returns `PROTOCOL_VIOLATION (0x0a)` instead of the `FRAME_ENCODING_ERROR (0x07)` required by RFC 9000.

## Standard Requirement

- RFC 9000 Section 19.7: [NEW_TOKEN Frames](https://www.rfc-editor.org/rfc/rfc9000.html#section-19.7)
- RFC 9000 Section 20.1: [Transport Error Codes](https://www.rfc-editor.org/rfc/rfc9000.html#section-20.1)

RFC 9000 Section 19.7 says the `Token` field of `NEW_TOKEN` must not be empty and that a client receiving a `NEW_TOKEN` frame with an empty Token field must treat it as `FRAME_ENCODING_ERROR`.

Section 20.1 defines `FRAME_ENCODING_ERROR (0x07)` for badly formatted frames and `PROTOCOL_VIOLATION (0x0a)` for compliance errors not covered by more specific codes. Section 19.7 also says clients must not send `NEW_TOKEN` frames and servers must treat receipt of `NEW_TOKEN` as `PROTOCOL_VIOLATION`, but that is a different condition. It applies to a server receiving any decoded `NEW_TOKEN` frame from a client, not to a client receiving an empty Token field, which has the more specific `FRAME_ENCODING_ERROR` rule.

## Relevant Source Code

`quic/s2n-quic-core/src/frame/new_token.rs:50-58`

```rust
let (token, buffer) = buffer.decode_slice_with_len_prefix::<VarInt>()?;
let token = token.into_less_safe_slice();
decoder_invariant!(!token.is_empty(), "empty Token field");
```

The empty token is rejected, so the bug is not acceptance of an invalid frame.

`quic/s2n-quic-transport/src/space/mod.rs:953-955`

```rust
let (frame, remaining) = payload
    .decode::<FrameMut>()
    .map_err(transport::Error::from)?;
```

`quic/s2n-quic-core/src/transport/error.rs:452-458`

```rust
match decoder_error {
    DecoderError::InvariantViolation(reason) => {
        Self::PROTOCOL_VIOLATION.with_reason(reason)
    }
    _ => Self::PROTOCOL_VIOLATION.with_reason("malformed packet"),
}
```

An empty `NEW_TOKEN` triggers `InvariantViolation("empty Token field")` during decoding, then the generic mapping converts it to `PROTOCOL_VIOLATION`.

`quic/s2n-quic-transport/src/space/application.rs:982-993`

```rust
if Config::ENDPOINT_TYPE.is_server() {
    return Err(transport::Error::PROTOCOL_VIOLATION
        .with_reason(Self::INVALID_FRAME_ERROR)
        .with_frame_type(frame.tag().into()));
}
```

This branch handles the case where a server successfully decodes a `NEW_TOKEN` from a client. It is not the empty-token decode path.

## Implementation Behavior

1. The empty `Token` is rejected during `NEW_TOKEN` decoding.
2. The resulting `DecoderError::InvariantViolation` is converted through `transport::Error::from`.
3. The frame is rejected, but the returned transport error code is `PROTOCOL_VIOLATION` instead of `FRAME_ENCODING_ERROR`.

## Runtime Evidence

I ran a minimal probe that constructed `0x07 0x00`, where `0x07` is the `NEW_TOKEN` frame type and `0x00` means `Token Length = 0`.

Command:

```bash
cargo run --manifest-path tmp/new_token_probe/Cargo.toml
```

Observed output:

```text
decoder_error=InvariantViolation("empty Token field")
transport_error=transport::Error { code: 10, description: "PROTOCOL_VIOLATION", reason: "empty Token field", frame_type: VarInt(0) }
transport_code=10
frame_encoding_error_code=7
protocol_violation_code=10
```

The run directly shows that the implementation returns `PROTOCOL_VIOLATION (10)` where RFC 9000 requires `FRAME_ENCODING_ERROR (7)`.

## Impact

The peer observes the wrong QUIC transport error code. The implementation rejects the illegal `NEW_TOKEN`, but it does not satisfy RFC 9000's explicit error-code requirement, which can break compliance tests and interoperability diagnostics.

## Fix Direction

Do not collapse this frame decode failure into `PROTOCOL_VIOLATION`. For empty `NEW_TOKEN` and similar malformed-frame paths, preserve or explicitly map the error to `FRAME_ENCODING_ERROR`.
