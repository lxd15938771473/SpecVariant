# `ACK + PADDING` on an unvalidated path is not counted in `bytes_in_flight`

## Summary

the original claim was too broad. The issue is not that arbitrary padding-only packets are missed. The confirmed case is narrower: on an unvalidated path, `quiche`'s first response is typically `ACK + PATH_CHALLENGE + PADDING`, which increases `bytes_in_flight`; a later response on the same still-unvalidated path can become `ACK + PADDING`. That packet is marked `in_flight`, but because `ack_eliciting = false`, `Sent.size` is stored as `0`, so `bytes_in_flight` does not increase.

## Standard Requirement

- RFC 9000 Section 13.2.7: [PADDING Frames Consume Congestion Window](https://www.rfc-editor.org/rfc/rfc9000.html#section-13.2.7)

Packets containing `PADDING` are considered in flight for congestion control. Padding does not elicit ACKs, but that does not mean it is free with respect to congestion window accounting.

## Relevant Source Code

`quiche/src/frame.rs:814-823`

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

Both `PADDING` and `ACK` are non-ack-eliciting.

`quiche/src/lib.rs:4668-4680`, `quiche/src/path.rs:370-377`, `quiche/src/path.rs:396-408`

```rust
if path.validation_requested() {
    let frame = frame::Frame::PathChallenge { data };

    if push_frame_to_pkt!(b, frames, frame, left) {
        challenge_data = Some(data);
        ack_eliciting = true;
        in_flight = true;
    }
}

pub fn request_validation(&mut self) {
    self.challenge_requested = true;
}

pub fn on_challenge_sent(&mut self) {
    self.promote_to(PathState::Validating);
    self.challenge_requested = false;
}
```

When path validation is required, `quiche` first sends a `PATH_CHALLENGE`. After that send, `challenge_requested` is cleared, so later packets need not include another `PATH_CHALLENGE`.

`quiche/src/lib.rs:5321-5358`

```rust
if frames.is_empty() {
    return Err(Error::Done);
}

if (has_initial || !path.validated()) &&
    pkt_type == Type::Short &&
    left >= 1
{
    let frame = frame::Frame::Padding { len: left };

    if push_frame_to_pkt!(b, frames, frame, left) {
        in_flight = true;
    }
}
```

`quiche` does not emit pure padding from nothing; however, when an `ACK` is already present on an unvalidated short-header path, it appends `PADDING` and marks the packet `in_flight`.

`quiche/src/lib.rs:5443-5451`, `quiche/src/recovery/congestion/recovery.rs:622-647`

```rust
let sent_pkt = recovery::Sent {
    pkt_num: pn,
    frames,
    time_sent: now,
    time_acked: None,
    time_lost: None,
    size: if ack_eliciting { written } else { 0 },
    ack_eliciting,
    in_flight,
    delivered: 0,
    delivered_time: now,
    first_sent_time: now,
    is_app_limited: false,
    tx_in_flight: 0,
    lost: 0,
};

let ack_eliciting = pkt.ack_eliciting;
let in_flight = pkt.in_flight;
let sent_bytes = pkt.size;

if in_flight {
    self.bytes_in_flight.add(sent_bytes, now);
}
```

The congestion accounting is incorrectly tied to `ack_eliciting`: a non-ack-eliciting packet can have `in_flight = true` but `sent_bytes = 0`.

## Implementation Behavior

On the first response to a new path, `PATH_CHALLENGE` is still pending, so the packet can be `ACK + PATH_CHALLENGE + PADDING`; that packet is ack-eliciting and accounted correctly. After the `PATH_CHALLENGE` is sent, the path can still be unvalidated. If only an ACK is needed at that point, `quiche` can send `ACK + PADDING`. Because `PING` is added only under the `ack_elicit_required || path.needs_ack_eliciting` condition at `quiche/src/lib.rs:5296-5304`, this packet can remain non-ack-eliciting and trigger the accounting gap.

## Inconsistency Reason

RFC 9000 requires packets containing `PADDING` to count as in flight. `quiche` first marks the packet `in_flight = true`, then uses `ack_eliciting` to decide whether `Sent.size` is `0`. For `ACK + PADDING`, that produces `in_flight = true` with `size = 0`, which is inconsistent with Section 13.2.7.

## Runtime Evidence

I ran the focused runtime test and observed the same behavior under both congestion controllers:

```text
cc=cubic
iter0 bif_before=0 bif_after=114 frames=[ACK delay=0 blocks=[3..3] ecn_counts=None, PATH_CHALLENGE data=[94, 4f, e4, dd, 22, 34, 09, b7], PADDING len=66]
iter1 bif_before=114 bif_after=114 frames=[ACK delay=0 blocks=[3..4] ecn_counts=None, PADDING len=75]
cc=bbr2_gcongestion
iter0 bif_before=0 bif_after=114 frames=[ACK delay=0 blocks=[3..3] ecn_counts=None, PATH_CHALLENGE data=[73, 26, 69, 38, 84, c4, 23, 30], PADDING len=66]
iter1 bif_before=114 bif_after=114 frames=[ACK delay=0 blocks=[3..4] ecn_counts=None, PADDING len=75]
test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 1095 filtered out; finished in 0.02s
```

In both runs, `iter0` increased `bytes_in_flight` from `0` to `114` for `ACK + PATH_CHALLENGE + PADDING`, while `iter1` left `bytes_in_flight` at `114` for `ACK + PADDING`.

## Impact

`ACK + PADDING` on an unvalidated path undercounts in-flight bytes, so congestion window usage is underestimated in this corner case.

## Fix Direction

Do not use `ack_eliciting` as the only condition for `Sent.size`. If a packet is marked `in_flight`, account its actual `written` bytes; at minimum, do so for in-flight packets containing `PADDING`. Ack elicitation and congestion accounting should be separate.
