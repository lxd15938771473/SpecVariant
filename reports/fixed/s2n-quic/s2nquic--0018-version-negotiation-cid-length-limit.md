# Version Negotiation decode incorrectly applies the v1 20-byte CID limit

## Summary

- The issue is real, but the mismatch is in `Version Negotiation` decode/acceptance, not in VN encode.

s2n-quic can read long connection IDs from an unsupported-version `Initial` and can encode them into a `Version Negotiation` packet, but it rejects that VN packet when either CID exceeds 20 bytes.

## Standard Requirement

- Standard: RFC 9000 Section 17.2 and Section 17.2.1
- Official link: https://www.rfc-editor.org/rfc/rfc9000#section-17.2.1

```text
A Version Negotiation packet is inherently not version specific.
Destination Connection ID (0..2040)
Source Connection ID (0..2040)
```

```text
In QUIC version 1, this value MUST NOT exceed 20 bytes.
... servers SHOULD be able to read longer connection IDs from other QUIC versions.
```

```text
Version-specific rules for the connection ID therefore MUST NOT
influence a decision about whether to send a Version Negotiation packet.
```

Interpretation: the `<= 20 bytes` rule applies to QUIC v1 long headers. `Version Negotiation` is version-independent and must be able to echo CIDs from unsupported or future versions, up to the 8-bit VN field limit of 255 bytes.

## Relevant Source Code

- `quic/s2n-quic-core/src/packet/initial.rs:84-93` intentionally skips CID validation for unsupported-version `Initial` packets so the server can form a VN packet.

```rust
// ... servers SHOULD be able to read longer connection IDs ...
let destination_connection_id =
    decoder.decode_checked_range::<DestinationConnectionIdLen>(&buffer)?;
let source_connection_id =
    decoder.decode_checked_range::<SourceConnectionIdLen>(&buffer)?;
```

- `quic/s2n-quic-core/src/packet/version_negotiation.rs:74-82` applies the normal long-header validators during VN decode.

```rust
validate_destination_connection_id_len(destination_connection_id.len())?;
...
validate_source_connection_id_len(source_connection_id.len())?;
```

- `quic/s2n-quic-core/src/packet/long.rs:73-80,109-112` makes those validators enforce the v1 limit.

```rust
len <= DESTINATION_CONNECTION_ID_MAX_LEN
len <= SOURCE_CONNECTION_ID_MAX_LEN
```

- `quic/s2n-quic-core/src/packet/version_negotiation.rs:134-142` copies the `Initial` packet CIDs directly into the VN packet.

```rust
destination_connection_id: initial_packet.source_connection_id(),
source_connection_id: initial_packet.destination_connection_id(),
```

## Implementation Behavior

The unsupported-version `Initial` path already accepts long CIDs and the VN encoder preserves them. The failure appears later: VN decode reuses the QUIC v1 `validate_*_connection_id_len` helpers and rejects any CID longer than 20 bytes.

## Runtime Evidence

Command:

```text
cd runtime/the requirement-recheck
cargo run --quiet
```

Observed output:

```text
cid_len_20_20: initial_ok, vn_encode_ok(dcid=20, scid=20), vn_decode_ok(dcid=20, scid=20)
cid_len_20_21: initial_ok, vn_encode_ok(dcid=21, scid=20), vn_decode_err(InvariantViolation("destination connection exceeds max length"))
cid_len_21_20: initial_ok, vn_encode_ok(dcid=20, scid=21), vn_decode_err(InvariantViolation("source connection exceeds max length"))
cid_len_32_32: initial_ok, vn_encode_ok(dcid=32, scid=32), vn_decode_err(InvariantViolation("destination connection exceeds max length"))
cid_len_255_255: initial_ok, vn_encode_ok(dcid=255, scid=255), vn_decode_err(InvariantViolation("destination connection exceeds max length"))
```

This run shows three concrete facts: unsupported-version `Initial` with long CIDs is accepted, VN encode preserves those long CIDs, and VN decode fails as soon as either CID exceeds 20 bytes.

## Inconsistency Reason

RFC 9000 treats `Version Negotiation` as version independent and explicitly allows it to echo connection IDs from other versions. s2n-quic follows that rule when reading an unsupported-version `Initial`, but not when decoding the VN packet that carries those echoed CIDs. The implementation therefore re-applies a QUIC v1-only limit in a version-independent path.

## Impact

A client or endpoint using this decoder can reject valid VN packets sent in response to reserved-version or future-version connection attempts that use CID lengths greater than 20 bytes.

## Fix Direction

Decode `Version Negotiation` with a VN-specific CID length rule based on the 8-bit length field (`0..255` bytes), and keep the 20-byte check only for QUIC v1 long-header packets.
