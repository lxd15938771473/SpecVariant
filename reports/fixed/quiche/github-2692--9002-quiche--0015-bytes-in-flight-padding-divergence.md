# PADDING bytes are omitted from bytes_in_flight

## Summary
quiche marks packets that contain `PADDING` as `in_flight`, but `Sent.size` stores the actual transmitted length only when the packet is `ack_eliciting`. `PADDING` is not ack-eliciting, so PADDING-only or ACK+PADDING packets enter recovery with `size = 0` and do not increase `bytes_in_flight`.

## Standard Requirement

[RFC 9002 Section 7.2, Variables of Interest](https://www.rfc-editor.org/rfc/rfc9002.html#section-7.2):

```text
bytes_in_flight:  The sum of the size in bytes of all sent packets
   that contain at least one ack-eliciting or PADDING frame and have
   not been acknowledged or declared lost.
```

[RFC 9002 Section 7.3, On Packets Sent](https://www.rfc-editor.org/rfc/rfc9002.html#section-7.3):

```text
OnPacketSent(packet_number, pn_space, ack_eliciting,
             in_flight, sent_bytes):
  ...
  sent_packets[pn_space][packet_number].sent_bytes = sent_bytes
  if (in_flight):
    if (ack_eliciting):
      time_of_last_ack_eliciting_packet[pn_space] = now()
    OnPacketSentCC(sent_bytes)
```

[RFC 9000 Section 13.2.7, PADDING Frames and Congestion Control](https://www.rfc-editor.org/rfc/rfc9000.html#section-13.2.7):

```text
Packets containing PADDING frames are considered to be in flight for
congestion control purposes [QUIC-RECOVERY].  Packets containing only
PADDING frames therefore consume congestion window but do not
generate acknowledgments that will open the congestion window.
```

The standard meaning is that `PADDING` does not elicit ACKs, but a packet containing `PADDING` must still count toward `bytes_in_flight` using its actual packet size.

## Relevant Source Code

`quiche/src/frame.rs:814`

```rust
pub fn ack_eliciting(&self) -> bool {
    !matches!(
        self,
        Frame::Padding { .. } |
            Frame::ACK { .. } |
            Frame::ApplicationClose { .. } |
            Frame::ConnectionClose { .. }
    )
}
```

`PADDING` is explicitly not ack-eliciting.

`quiche/src/lib.rs:5328`

```rust
if (has_initial || !path.validated()) &&
    pkt_type == Type::Short &&
    left >= 1
{
    let frame = frame::Frame::Padding { len: left };

    if push_frame_to_pkt!(b, frames, frame, left) {
        in_flight = true;
    }
}

if b.off() - payload_offset < PAYLOAD_MIN_LEN {
    let frame = frame::Frame::Padding {
        len: PAYLOAD_MIN_LEN - payload_len,
    };

    if push_frame_to_pkt!(b, frames, frame, left) {
        in_flight = true;
    }
}
```

The send path sets `in_flight = true` because of `PADDING`.

`quiche/src/lib.rs:5443`

```rust
let sent_pkt = recovery::Sent {
    ...
    size: if ack_eliciting { written } else { 0 },
    ack_eliciting,
    in_flight,
    ...
};
```

Even when a non-ack-eliciting packet is `in_flight`, its `size` is recorded as `0`.

`quiche/src/recovery/congestion/recovery.rs:622`

```rust
let ack_eliciting = pkt.ack_eliciting;
let in_flight = pkt.in_flight;
let sent_bytes = pkt.size;

if in_flight {
    self.epochs[epoch].in_flight_count += 1;
    self.bytes_in_flight.add(sent_bytes, now);
    self.set_loss_detection_timer(handshake_status, now);
}
```

`quiche/src/recovery/bytes_in_flight.rs:57`

```rust
pub(crate) fn add(&mut self, delta: usize, now: Instant) {
    if delta == 0 {
        return;
    }

    self.bytes_in_flight += delta;
}
```

When `sent_bytes = 0`, `bytes_in_flight` is not increased. gcongestion recovery uses the same `sent_bytes = pkt.size` value before calling `bytes_in_flight.add(sent_bytes, now)`.

## Implementation Behavior

A packet containing `PADDING` can have `in_flight = true`. If it contains no other ack-eliciting frame, it also has `ack_eliciting = false`, so the send record becomes:

```text
written > 0
size = 0
bytes_in_flight.add(0) returns
```

Therefore, the actual bytes from PADDING-only or ACK+PADDING packets do not enter congestion accounting.

## Inconsistency Reason

The standard requires packets containing `PADDING` to count toward `bytes_in_flight` by packet size. quiche uses only `ack_eliciting` to decide `Sent.size`, so non-ack-eliciting but still in-flight `PADDING` bytes are recorded as zero. The evidence supports the described behavior.

## Runtime Evidence

A minimal Rust probe was run:

```text
rustc --edition=2021 runtime/bytes_in_flight_padding_probe.rs -o runtime/bytes_in_flight_padding_probe.exe
runtime/bytes_in_flight_padding_probe.exe
```

The probe modeled the relevant send record and recovery accounting and observed:

```text
written=1200
padding_ack_eliciting=false
padding_in_flight=true
sent_size=0
bytes_in_flight_after_send=0
```

The auxiliary source probe was also re-run and reported `bytes_in_flight_padding_divergence` as `ok: true`, confirming that the source still contains the same `in_flight`, `ack_eliciting`, and `Sent.size` signals.

## Impact

PADDING-only or ACK+PADDING traffic might not consume congestion window. The sender can underestimate bytes in flight and bypass the RFC congestion-control constraint for PADDING traffic.

## Fix Direction

`Sent.size` should be determined by `in_flight`, not only by `ack_eliciting`:

```rust
size: if in_flight { written } else { 0 },
```
