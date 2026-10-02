# DATA_BLOCKED / STREAM_DATA_BLOCKED are not retransmitted after loss

- This is a real behavior difference.
- Requirement strength: `SHOULD`, not a `MUST`-level interoperability error.

## Standard Requirement

- RFC 9000 Section 4.1: [Data Flow Control](https://www.rfc-editor.org/rfc/rfc9000.html#section-4.1)
- RFC 9000 Section 13.3: [Retransmission of Information](https://www.rfc-editor.org/rfc/rfc9000.html#section-13.3)
- RFC 9000 Section 19.12: [DATA_BLOCKED Frames](https://www.rfc-editor.org/rfc/rfc9000.html#section-19.12)
- RFC 9000 Section 19.13: [STREAM_DATA_BLOCKED Frames](https://www.rfc-editor.org/rfc/rfc9000.html#section-19.13)

Section 4.1 says a sender should send `STREAM_DATA_BLOCKED` or `DATA_BLOCKED` while blocked, but also clarifies that a blocked sender is not required to send those frames and the receiver cannot depend on them.

Section 13.3 adds the retransmission rule: if the most recent frame for a scope is lost, a new frame is sent while the endpoint remains blocked on the corresponding limit.

Sections 19.12 and 19.13 define `DATA_BLOCKED` and `STREAM_DATA_BLOCKED` themselves as `SHOULD send` signals.

## Relevant Source Code

`quiche/src/lib.rs:4757-4764`

```rust
if let Some(limit) = self.blocked_limit {
    let frame = frame::Frame::DataBlocked { limit };

    if push_frame_to_pkt!(b, frames, frame, left) {
        self.blocked_limit = None;
```

After sending `DATA_BLOCKED`, the implementation immediately clears `blocked_limit`.

`quiche/src/lib.rs:4925-4936`

```rust
for (stream_id, limit) in self.streams.blocked().map(|(&k, &v)| (k, v)) {
    let frame = frame::Frame::StreamDataBlocked { stream_id, limit };

    if push_frame_to_pkt!(b, frames, frame, left) {
        self.streams.remove_blocked(stream_id);
```

After sending `STREAM_DATA_BLOCKED`, the stream is removed from the blocked set.

`quiche/src/lib.rs:6078-6087`

```rust
if sent < cap {
    let max_off = stream.send.max_off();

    if stream.send.blocked_at() != Some(max_off) {
        stream.send.update_blocked_at(Some(max_off));
        self.streams.insert_blocked(stream_id, max_off);
    }
}
```

The same `max_off` is requeued only if `blocked_at` changes; the loss path does not clear this state.

`quiche/src/lib.rs:4333-4337`

```rust
frame::Frame::DataBlocked { .. } |
frame::Frame::StreamDataBlocked { .. } => (),
```

Loss processing explicitly ignores `DATA_BLOCKED` and `STREAM_DATA_BLOCKED`.

`quiche/src/lib.rs:4270-4282`

```rust
frame::Frame::StreamsBlockedBidi { limit } => {
    self.streams_blocked_bidi_state.force_retransmit_sent_limit_eq(limit);
},
```

By contrast, `STREAMS_BLOCKED` has explicit retransmission state after loss.

## Runtime Evidence

I ran two targeted rechecks:

- `tests::recheck_data_blocked_loss_behavior`
- `tests::recheck_stream_data_blocked_loss_behavior`

The `DATA_BLOCKED` case observed that the first packet contained `DATA_BLOCKED { limit: 30 }`. After `trigger_ack_based_loss()`, the next packet no longer contained `DATA_BLOCKED`, and after delivery the server still had `server.data_blocked_recv_count == 0`.

The `STREAM_DATA_BLOCKED` case observed that the first packet contained `STREAM_DATA_BLOCKED { stream_id: 0, limit: 15 }`. After loss, the next packet no longer contained `STREAM_DATA_BLOCKED`, and after delivery the server still had `server.stream_data_blocked_recv_count == 0`.

## Final Assessment

`quiche` chooses to send blocked signals, but when the most recent signal is lost and the endpoint remains blocked, it does not send a new `DATA_BLOCKED` or `STREAM_DATA_BLOCKED`. That conflicts with the retransmission semantics in RFC 9000 Section 13.3. Considering Sections 4.1, 19.12, and 19.13, this should be treated as a real `SHOULD`-level compliance issue rather than a `MUST`-level protocol error.
