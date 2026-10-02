# future-version long-header type bits are interpreted with the v1 table before version negotiation

The implementation reads the invariant long-header fields, but it still chooses `Initial` / `ZeroRtt` / `Handshake` / `Retry` from the v1 first-byte table before version negotiation. For QUIC v2, that is observable: a v2 Initial can be classified as `ZeroRtt`, which changes both Version Negotiation behavior and pre-negotiation CID-length validation.

## Standard

```text
The header form bit, Destination and Source Connection ID lengths,
Destination and Source Connection ID fields, and Version fields of a
long header packet are version independent.  The other fields in the
first byte are version specific.
```

Two supporting rules matter here.

- `rfc9000` lines 1515-1519: the first packet of an unsupported version can use different semantics and encodings for any version-specific field.
- `rfc9000` lines 5125-5129: version-specific CID rules MUST NOT influence whether a Version Negotiation packet is sent.

QUIC v2 shows that this is not hypothetical. `rfc9369` lines 145-154 redefine the long-header type bits:

```text
Initial: 0b01
0-RTT:   0b10
Handshake: 0b11
Retry:   0b00
```

## Code

`quic/s2n-quic-core/src/packet/mod.rs:214-250`

```rust
macro_rules! long_packet {
    ($struct:ident, $handler:ident) => {{
        let (version, _peek) = peek.decode()?;
        if version == version_negotiation::VERSION {
            version_negotiation!(version)
        } else {
            let (packet, buffer) = $struct::decode(tag, version, buffer)?;
            let output = self.$handler(packet)?;
            Ok((output, buffer))
        }
    }};
}

match tag >> 4 {
    initial_tag!() => long_packet!(ProtectedInitial, handle_initial_packet),
    zero_rtt_tag!() => long_packet!(ProtectedZeroRtt, handle_zero_rtt_packet),
    handshake_tag!() => long_packet!(ProtectedHandshake, handle_handshake_packet),
    retry_tag!() => long_packet!(ProtectedRetry, handle_retry_packet),
```

The dispatch key is `tag >> 4`, so non-zero versions still enter a v1 packet-type branch before version support is checked.

`quic/s2n-quic-core/src/packet/initial.rs:82-97`

```rust
let mut decoder = HeaderDecoder::new_long(&buffer);
let destination_connection_id =
    decoder.decode_checked_range::<DestinationConnectionIdLen>(&buffer)?;
let source_connection_id =
    decoder.decode_checked_range::<SourceConnectionIdLen>(&buffer)?;
```

`Initial` deliberately avoids the v1 `<= 20` CID limit here so that future-version packets can still reach Version Negotiation.

`quic/s2n-quic-core/src/packet/zero_rtt.rs:70-79`

```rust
let mut decoder = HeaderDecoder::new_long(&buffer);
let destination_connection_id = decoder.decode_destination_connection_id(&buffer)?;
let source_connection_id = decoder.decode_source_connection_id(&buffer)?;
```

`ZeroRtt` applies the v1 CID-length checks immediately.

`quic/s2n-quic-transport/src/endpoint/version.rs:94-119`

```rust
let packet = match packet {
    ProtectedPacket::Initial(packet) => {
        if is_supported!(packet, publisher) {
            return Ok(());
        }
        packet
    }
    ProtectedPacket::ZeroRtt(packet) => {
        if is_supported!(packet, publisher) {
            return Ok(());
        }
        return Err(Error);
    }
    ProtectedPacket::VersionNegotiation(_packet) => {
        return Ok(());
    }
    _ => return Ok(()),
};
```

Unsupported `Initial` packets can reach the Version Negotiation transmit path. Unsupported `ZeroRtt` packets do not.

## Runtime Evidence

Reproducer files:

- `runtime/the requirement-recheck/src/main.rs`
- `runtime/the requirement-recheck/run.log`

Command:

```text
cd runtime/the requirement-recheck
cargo run --quiet
```

Output:

```text
rfc9369_v2_client_initial_header: ok variant=ZeroRtt input_len=1199 first_packet_len=16 remaining_len=1183
unsupported_initial_with_21_byte_cids: ok variant=Initial input_len=56 first_packet_len=56 remaining_len=0
unsupported_zero_rtt_with_21_byte_cids: err=InvariantViolation("destination connection exceeds max length")
```

What this shows:

- The protected client Initial header from `rfc9369` Appendix A.2 is parsed as `ZeroRtt`, not `Initial`.
- An unsupported-version `Initial`-style packet with 21-byte CIDs still parses as `Initial`, matching the code path that preserves Version Negotiation eligibility.
- An unsupported-version `ZeroRtt`-style packet with the same 21-byte CID lengths is rejected by the v1 CID-length rule before it can follow the `Initial` Version Negotiation path.

## Decision

This is a real issue. Version-specific first-byte bits are affecting pre-negotiation control flow, even though those bits are not invariant across versions. The concrete v2 example proves that a future-version Initial can be misclassified as `ZeroRtt`, and the parser/endpoint split then changes whether Version Negotiation is possible and whether v1 CID-length limits are applied too early.

## Fix Direction

Parse only invariant long-header fields before version negotiation. Defer packet-type-specific dispatch and version-specific CID validation until the version is known to be supported, or add an unsupported-version path that uses only invariant fields plus the Version field to decide Version Negotiation behavior.

## Merged Redundant Reports

The following report described the same root cause and the same required fix, so it was merged into this report and removed:

- `0016-the requirement.md`: future-version packet type bits are interpreted with the QUIC v1 table before version negotiation.
