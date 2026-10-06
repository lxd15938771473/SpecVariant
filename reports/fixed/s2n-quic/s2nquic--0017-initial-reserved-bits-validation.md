# Initial packets do not reject non-zero reserved bits

## Summary

RFC 9000 requires all long-header packet types to reject non-zero reserved bits after removing both header protection and packet protection. `Handshake` enforces this, but `Initial` does not. The server receive path decrypts `Initial` packets and continues without any follow-up reserved-bit check, so a valid protected `Initial` with reserved bits `0x0c` is accepted.

`ZeroRtt::decrypt` also lacks the check, but the current transport path does not actually unprotect and decrypt 0-RTT packets, so this report does not count 0-RTT as a confirmed reachable issue.

## Standard Requirement

- RFC 9000 Section 17.2: [Long Header Packets](https://www.rfc-editor.org/rfc/rfc9000.html#section-17.2)

```text
Reserved Bits:  Two bits (those with a mask of 0x0c) of byte 0 are
   reserved across multiple packet types.  These bits are protected
   using header protection; see Section 5.4 of [QUIC-TLS].  The value
   included prior to protection MUST be set to 0.  An endpoint MUST
   treat receipt of a packet that has a non-zero value for these bits
   after removing both packet and header protection as a connection
   error of type PROTOCOL_VIOLATION.
```

`Initial`, `0-RTT`, and `Handshake` all carry `Reserved Bits`; see RFC 9000 Figures 15-17 in Section 17.2. RFC 9001 Section 9.5 also requires header protection removal, packet number recovery, and packet protection removal to be applied together.

## Relevant Source Code

### `quic/s2n-quic-core/src/packet/handshake.rs:160-170`

```rust
        //= https://www.rfc-editor.org/rfc/rfc9000#section-17.2
        //# The value
        //# included prior to protection MUST be set to 0.  An endpoint MUST
        //# treat receipt of a packet that has a non-zero value for these bits
        //# after removing both packet and header protection as a connection
        //# error of type PROTOCOL_VIOLATION.
        if header[0] & super::long::RESERVED_BITS_MASK != 0 {
            return Err(transport::Error::PROTOCOL_VIOLATION
                .with_reason("reserved bits are non-zero")
                .into());
        }
```

`Handshake` has the required check.

### `quic/s2n-quic-core/src/packet/initial.rs:176-191`

```rust
        let (header, payload) = crate::crypto::decrypt(crypto, packet_number, payload)?;

        let header = header.into_less_safe_slice();

        let destination_connection_id = destination_connection_id.get(header);
        let source_connection_id = source_connection_id.get(header);
        let token = token.get(header);

        Ok(Initial {
            version,
            destination_connection_id,
            source_connection_id,
            token,
            packet_number,
            payload,
        })
```

`Initial::decrypt` returns cleartext directly and never checks `RESERVED_BITS_MASK`.

### `quic/s2n-quic-transport/src/endpoint/initial.rs:121-126`

```rust
        let packet = packet
            .unprotect(&initial_header_key, largest_packet_number)
            .map_err(|_| transport::Error::from(tls::Error::DECODE_ERROR))?;
        let packet = packet
            .decrypt(&initial_key)
            .map_err(|_| transport::Error::from(tls::Error::DECRYPT_ERROR))?;
```

The server startup path uses the unchecked `Initial::decrypt` result.

## Implementation Behavior

- Observed affected case: `Initial`. Core decrypt lacks the reserved-bit check, and the server receive path consumes that result.
- Caveat only: `0-RTT`. `quic/s2n-quic-core/src/packet/zero_rtt.rs:147-160` also lacks the check, but `quic/s2n-quic-transport/src/connection/connection_impl.rs:1986-2010` currently only records receipt and returns `Ok(())`; it does not unprotect or decrypt the packet. `quic/s2n-quic-transport/src/space/session_context.rs:376-377` also shows the 0-RTT header key is not stored yet.

## Inconsistency Reason

RFC 9000 applies the reserved-bit rule to long-header packets generally, not just `Handshake`. The implementation enforces the rule for `Handshake`, but not for `Initial`. Because the server receive path directly uses `Initial::decrypt`, a malformed but cryptographically valid `Initial` is accepted instead of causing `PROTOCOL_VIOLATION`.

## Runtime Evidence

Command:

```bash
cd tmp/reserved-bits-repro
cargo run --quiet
```

Output:

```text
initial_core_accepts_reserved_bits=true reserved_bits_after_unprotect=0x0c
zero_rtt_core_accepts_reserved_bits=true reserved_bits_after_unprotect=0x0c
```

The reproducer builds valid protected packets whose reserved bits are non-zero before protection, then runs `ProtectedPacket::decode -> unprotect -> decrypt`. The first line confirms the `Initial` path accepts reserved bits `0x0c` after unprotect, which is the required rejection point from RFC 9000 Section 17.2. The second line confirms the same missing check in the 0-RTT core decoder, but it does not change the transport-level caveat above.

## Impact

A peer can send a cryptographically valid but non-compliant `Initial` and avoid the required connection error. This is a real protocol-compliance bug and can produce behavior that differs from stricter QUIC implementations. The 0-RTT decoder should be fixed at the same time to avoid carrying the same bug into future receive-path support.

## Fix Direction

Add the same reserved-bit validation used by `Handshake` to `Initial`, and ideally also to `ZeroRtt`. A shared post-decrypt helper for long-header packets would keep all three packet types aligned and avoid this kind of drift.
