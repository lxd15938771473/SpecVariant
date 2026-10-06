# PATH_RESPONSE padding can exceed amplification credit

## Summary

RFC 9000 permits expanding a `PATH_RESPONSE` datagram to `1200` bytes only if the expanded datagram stays within the anti-amplification limit. s2n-quic treats any positive `tx_allowance` as sendable, then lets path-validation datagrams use the MTU-sized `1200` byte buffer, so a server with only a few bytes of credit can still select a 1200-byte `PATH_RESPONSE` datagram.

## Standard Requirement

Official standard: <https://www.rfc-editor.org/rfc/rfc9000#section-8.2.2>

RFC 9000 Section 8.2.2, "Path Validation Responses":

```text
An endpoint MUST expand datagrams that contain a PATH_RESPONSE frame
to at least the smallest allowed maximum datagram size of 1200 bytes.
This verifies that the path is able to carry datagrams of this size
in both directions.  However, an endpoint MUST NOT expand the
datagram containing the PATH_RESPONSE if the resulting data exceeds
the anti-amplification limit.
```

So the `1200` byte expansion is forbidden when it would exceed the remaining anti-amplification budget.

## Relevant Source Code

`quic/s2n-quic-transport/src/path/mod.rs:212-228`

```rust
pub fn on_bytes_received(&mut self, bytes: usize) -> AmplificationOutcome {
    // ...
    if let State::AmplificationLimited { tx_allowance } = &mut self.state {
        *tx_allowance +=
            bytes.saturating_mul(self.anti_amplification_multiplier as usize) as u32;
    }
}
```

Received bytes increase `tx_allowance`.

`quic/s2n-quic-transport/src/path/mod.rs:556-564`

```rust
pub fn at_amplification_limit(&self) -> bool {
    match self.state {
        State::Validated => false,
        State::AmplificationLimited { tx_allowance } => tx_allowance == 0,
    }
}
```

The path is blocked only when `tx_allowance == 0`.

`quic/s2n-quic-transport/src/path/mod.rs:508-518`

```rust
pub fn clamp_datagram_size(
    &self,
    requested_size: usize,
    transmission_mode: transmission::Mode,
) -> usize {
    debug_assert!(!self.at_amplification_limit());

    requested_size.min(self.max_datagram_size(transmission_mode))
}
```

The clamp ignores the remaining `tx_allowance`; it only applies the mode's max datagram size.

`quic/s2n-quic-transport/src/connection/transmission.rs:145-155`

```rust
let max_datagram_size = self
    .context
    .path()
    .clamp_datagram_size(buffer.len(), self.context.transmission_mode);

let encoder = EncoderBuffer::new(&mut buffer[..max_datagram_size]);
let initial_capacity = encoder.capacity();
```

The outgoing encoder capacity comes from `clamp_datagram_size`.

`quic/s2n-quic-transport/src/connection/transmission.rs:353-365`

```rust
//= https://www.rfc-editor.org/rfc/rfc9000#section-8.2.2
//# An endpoint MUST expand datagrams that contain a PATH_RESPONSE frame
//# to at least the smallest allowed maximum datagram size of 1200 bytes.
// ...
if !path.is_validated() && path.has_transmission_interest() {
    self.context.min_packet_len = Some(encoder.capacity());
}
```

For pending path-validation frames, padding is driven by the full encoder capacity.

## Implementation Behavior

An unvalidated server path that receives `3` bytes gets `9` bytes of credit. Since `tx_allowance > 0`, s2n-quic does not consider the path amplification-limited. In `PathValidationOnly`, `clamp_datagram_size(1200, ...)` still returns `1200`, allowing the later `PATH_RESPONSE` padding path to exceed the remaining credit.

## Inconsistency Reason

RFC 9000 checks the final expanded datagram against the anti-amplification limit. s2n-quic only checks whether any credit remains before choosing the datagram size, so it can select `1200` even when the remaining credit is much smaller.

## Runtime Evidence

Focused temporary test command:

```text
cargo test -p s2n-quic-transport path::tests::path_response_padding_can_exceed_credit_document_recheck -- --exact --nocapture
```

Temporary test body:

```rust
#[test]
fn path_response_padding_can_exceed_credit_document_recheck() {
    let mut path = testing::helper_path_server();
    let amplification_outcome = path.on_bytes_received(3);

    assert!(amplification_outcome.is_inactivate_path_unblocked());
    assert!(!path.at_amplification_limit());
    assert_eq!(
        path.clamp_datagram_size(
            MINIMUM_MAX_DATAGRAM_SIZE as usize,
            transmission::Mode::PathValidationOnly
        ),
        MINIMUM_MAX_DATAGRAM_SIZE as usize
    );
}
```

Result: passed, exit code `0`. The pass confirms that with only `9` bytes of credit, the path still permits a `1200` byte path-validation datagram size.

Key stdout:

```text
running 1 test
test path::tests::path_response_padding_can_exceed_credit_document_recheck ... ok

test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 354 filtered out
```

The temporary test was removed after execution.

## Impact

Before address validation, a server can expand a `PATH_RESPONSE` datagram beyond its remaining anti-amplification budget, weakening the amplification defense for this path-validation case.

## Fix Direction

When padding a `PATH_RESPONSE` datagram, cap the expanded size by remaining anti-amplification credit. If `1200` would exceed that credit, send the unexpanded datagram.
