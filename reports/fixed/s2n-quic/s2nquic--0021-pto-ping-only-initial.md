# PTO probe can emit a PING-only Initial

s2n-quic can emit an `Initial` packet whose payload contains `PING` and padding, but no `ACK` and no `CRYPTO`. That packet shape is reachable during client PTO probing after Initial CRYPTO has already been acknowledged.

## Standard Requirement

- RFC 9000 Section 17.2.2: [Initial Packet](https://www.rfc-editor.org/rfc/rfc9000.html#section-17.2.2)

```text
The payload of an Initial packet includes a CRYPTO frame (or frames)
   containing a cryptographic handshake message, ACK frames, or both.
PING, PADDING, and CONNECTION_CLOSE frames of type 0x1c are also permitted.
```

- RFC 9002 Section 6.2.4: [Sending Probe Packets](https://www.rfc-editor.org/rfc/rfc9002.html#section-6.2.4)

```text
When the PTO fires, the client MUST send a Handshake packet if it
has Handshake keys, otherwise it MUST send an Initial packet in a UDP
datagram with a payload of at least 1200 bytes.
```

- RFC 9002 Section 6.2.4: [Sending Probe Packets](https://www.rfc-editor.org/rfc/rfc9002.html#section-6.2.4)

```text
When there is no data to send, the sender SHOULD send
a PING or other ack-eliciting frame in a single packet, rearming the PTO timer.
```

Interpretation: RFC 9002 explains why a PTO probe may need to send an `Initial` and why `PING` may be used when there is no data. But RFC 9000 Section 17.2.2 still constrains the payload of every `Initial`: it must contain `CRYPTO`, `ACK`, or both. `PING` is only additional, not a substitute.

## Relevant Source Code

### `quic/s2n-quic-transport/src/transmission/early.rs:31-41`

```rust
let did_send_ack = self.ack_manager.on_transmit(context);
...
let _ = self.crypto_stream.tx.on_transmit((), context);
...
self.recovery_manager.on_transmit(context);
```

`Initial` payload assembly is ordered as `ACK -> CRYPTO -> PTO probe`.

### `quic/s2n-quic-transport/src/sync/data_sender/writer.rs:86-93`

```rust
// Allow already transmitted, unacked crypto frames to be included in
// probe packets in anticipation the crypto frames were lost.
const RETRANSMIT_IN_PROBE: bool = true;
```

Probe retransmission only helps while Initial CRYPTO is still unacknowledged.

### `quic/s2n-quic-transport/src/space/initial.rs:704-725`

`Initial` ACK handling forwards the ACK to recovery, but unlike the Handshake/Application ACK paths it does not call `path.on_peer_validated()`. See the contrast at `quic/s2n-quic-transport/src/space/handshake.rs:582-583` and `quic/s2n-quic-transport/src/space/application.rs:880-881`.

### `quic/s2n-quic-transport/src/recovery/manager.rs:341-349`

```rust
if !ack_eliciting_packets_in_flight && active_path.is_peer_validated() {
    self.pto.cancel();
    return;
}
```

So after Initial CRYPTO is acknowledged, PTO can remain armed while the peer is still not validated.

### `quic/s2n-quic-core/src/recovery/pto.rs:107-118`

```rust
if !context.ack_elicitation().is_ack_eliciting() {
    let frame = frame::Ping;
    ensure!(context.write_frame_forced(&frame).is_some());
}
```

If the probe packet would otherwise contain no ack-eliciting frame, PTO forcibly injects `PING`.

## Runtime Evidence

The old runtime evidence in this report was not behavioral. `runtime/check_candidate.py:122-126` only checked that a source file was non-empty:

```python
return bool(text.strip()), {
    "checked_path": relpath,
    "bytes_read": len(text.encode("utf-8", errors="replace")),
    "source_present": True,
}
```

This recheck used a focused temporary unit test and then removed that test after capture.

Command:

```text
cargo test -p s2n-quic-transport pto_probe_can_encode_ping_only_initial_after_initial_crypto_is_acked --lib -- --nocapture
```

Observed output:

```text
running 1 test
test space::initial::tests::pto_probe_can_encode_ping_only_initial_after_initial_crypto_is_acked ... ok

test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 354 filtered out; finished in 0.00s
```

What the test checked:

- a client `Initial` packet with CRYPTO was sent first;
- that Initial CRYPTO was then acknowledged;
- no Initial `ACK` remained pending;
- PTO fired while the peer was still not validated;
- the next emitted `Initial` contained `PING` and padding, but no `ACK` and no `CRYPTO`.

After capture, the temporary test was removed and the existing test `cargo test -p s2n-quic-transport retry_token_is_repeated_in_subsequent_initial_packets --lib -- --nocapture` also passed.

## Inconsistency Reason

The implementation follows RFC 9002's PTO rule by forcing an ack-eliciting probe, but in this reachable `Initial`-space state it satisfies that rule using `PING` alone. RFC 9000 Section 17.2.2 is stricter for `Initial` payloads and still requires `ACK` or `CRYPTO`. The emitted packet is therefore non-compliant.

## Impact

A client can send a syntactically valid but non-compliant `Initial` probe during loss recovery. This can produce interoperability differences against endpoints that enforce the RFC 9000 `Initial` payload rule strictly.

## Fix Direction

When probing in `Initial` space, s2n-quic should only emit an `Initial` if it can include qualifying `CRYPTO` or `ACK`. If neither is available, the implementation should avoid producing a PING-only `Initial` and instead defer probing to a packet number space whose payload rules permit that shape.
