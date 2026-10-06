# Sender-induced packet number gaps reduce packet-threshold tolerance

## Summary
quiche intentionally skips packet numbers for optimistic ACK mitigation, but its packet-threshold loss detection still uses raw packet-number arithmetic. With sent packet numbers `[0, 1, 3]` and skipped packet number `2`, ACKing `3` incorrectly declares packet `0` lost after only two later sent packets.

## Standard Requirement

Official standard: [RFC 9002 Section 6.1.1 "Packet Threshold"](https://www.rfc-editor.org/rfc/rfc9002#section-6.1.1) and [Appendix A.10 "DetectAndRemoveLostPackets"](https://www.rfc-editor.org/rfc/rfc9002#appendix-A.10):

```text
A packet is declared lost if it meets all of the following
conditions:

*  The packet is unacknowledged, in flight, and was sent prior to an
   acknowledged packet.

*  The packet was sent kPacketThreshold packets before an
   acknowledged packet (Section 6.1.1), or it was sent long enough in
   the past (Section 6.1.2).

The RECOMMENDED initial value for the packet reordering threshold
(kPacketThreshold) is 3, based on best practices for TCP loss
detection [RFC5681] [RFC6675]. In order to remain similar to TCP,
implementations SHOULD NOT use a packet threshold less than 3.

// Note: The use of kPacketThreshold here assumes that there
// were no sender-induced gaps in the packet number space.
largest_acked_packet[pn_space] >=
  unacked.packet_number + kPacketThreshold
```

The threshold is a distance in sent packets. The appendix's numeric shortcut is valid only when the sender did not create packet-number gaps.

## Relevant Source Code

`quiche/src/packet.rs:1060` decides when to skip a packet number after handshake completion:

```rust
pub fn should_skip_pn(&self, handshake_completed: bool) -> bool {
    let no_current_skip_packet = self.skip_pn.is_none();
    let counter_expired = match self.skip_pn_counter {
        Some(counter) => counter == 0,
        None => false,
    };

    counter_expired && no_current_skip_packet && handshake_completed
}
```

`quiche/src/lib.rs:4389` records the skipped packet number and increments `next_pkt_num`:

```rust
if pkt_num_manager.should_skip_pn(self.handshake_completed) {
    pkt_num_manager.set_skip_pn(Some(self.next_pkt_num));
    self.next_pkt_num += 1;
};
let pn = self.next_pkt_num;
```

`quiche/src/lib.rs:8339` passes `skip_pn` into ACK processing, but only validates optimistic ACK state when a larger ACK arrives:

```rust
self.pkt_num_manager.skip_pn(),

if let Some((largest_acked, skip_pn)) = largest_acked.zip(skip_pn) {
    if largest_acked > skip_pn {
        self.pkt_num_manager.set_skip_pn(None);
    }
}
```

Legacy recovery still uses raw packet-number distance:

```rust
// quiche/src/recovery/congestion/recovery.rs:250
if unacked.time_sent <= lost_send_time ||
    largest_acked >= unacked.pkt_num + pkt_thresh
{
```

gcongestion uses the same numeric check:

```rust
// quiche/src/recovery/gcongestion/recovery.rs:287
let loss_by_pkt = match pkt_thresh {
    Some(pkt_thresh) => largest_acked >= *pkt_num + pkt_thresh,
    None => false,
};
```

## Implementation Behavior

The implementation can create a sender-induced gap (`skip_pn`), and it tracks that gap for optimistic ACK validation. However, loss detection does not subtract or otherwise ignore skipped packet numbers when applying `kPacketThreshold`.

## Inconsistency Reason

[RFC 9002 Section 6.1.1](https://www.rfc-editor.org/rfc/rfc9002.html#section-6.1.1) says a packet is lost after it was sent `kPacketThreshold` packets before an acknowledged packet. quiche instead checks `largest_acked >= pkt_num + pkt_thresh`. That matches Appendix A only under the stated assumption of no sender-induced gaps, but quiche deliberately creates such gaps.

## Runtime Evidence

Focused test source:

```text
runtime-recheck-0013/sender_gap_threshold_reproducer.rs
```

Command, run from `implementions/quiche`:

```text
cargo test -p quiche codex_tmp_sender_induced_gap_must_not_reduce_packet_threshold --features gcongestion --release -- --nocapture
```

The test sends packet numbers `[0, 1, 3]`, marks `2` as the sender-skipped packet number, then ACKs `3`. Expected result: packet `0` is not lost because only two later sent packets exist.

Observed result:

```text
cubic: sent_packet_numbers=[0,1,3] skipped=2 acked=3 lost_packets=1 lost_bytes=1000
bbr2_gcongestion: sent_packet_numbers=[0,1,3] skipped=2 acked=3 lost_packets=1 lost_bytes=1000
reno: sent_packet_numbers=[0,1,3] skipped=2 acked=3 lost_packets=1 lost_bytes=1000
test result: FAILED. 0 passed; 3 failed; 1093 filtered out
```

The command exited with code `101`, which is the expected reproducer signal for the failing assertions above.

## Impact

Sender-created packet-number gaps consume the packet reordering threshold. This can cause premature loss declaration, unnecessary retransmission, and avoidable congestion-control reaction across legacy recovery and gcongestion.

## Fix Direction

Apply packet-threshold loss detection over actual sent packet order/count, or compensate for known skipped packet numbers before comparing against `kPacketThreshold`. Keep optimistic ACK validation separate from loss-threshold accounting.
