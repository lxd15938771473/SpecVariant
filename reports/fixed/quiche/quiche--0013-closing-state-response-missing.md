# Closing state does not retransmit CONNECTION_CLOSE for later attributed packets

## Summary

- After `quiche` sends its first local `CONNECTION_CLOSE`, it immediately enters the send-suppressed path driven by `draining_timer`. Later packets that still belong to the connection can be consumed by `recv()`, but they do not trigger another `CONNECTION_CLOSE` response.

## Standard Requirement

- RFC 9000 Section 10.2.1: [Closing Connection State](https://www.rfc-editor.org/rfc/rfc9000.html#section-10.2.1)
- RFC 9000 Section 10.2.2: [Draining Connection State](https://www.rfc-editor.org/rfc/rfc9000.html#section-10.2.2)
- RFC 9000 Section 19.19: [CONNECTION_CLOSE Frames](https://www.rfc-editor.org/rfc/rfc9000.html#section-19.19)

Section 10.2.1 requires an endpoint that has sent `CONNECTION_CLOSE` to enter the closing state, and an endpoint in that state sends a packet containing `CONNECTION_CLOSE` in response to incoming packets it attributes to the connection. Section 10.2.2 is different: only the draining state forbids all packet transmission. Section 19.19 also says endpoints should be prepared to retransmit `CONNECTION_CLOSE` when they receive more packets on a terminated connection.

The practical requirement is that a local close first behaves as `closing`, not immediately as `draining`.

## Relevant Source Code

- `quiche/src/lib.rs:7553`: `close()` records only `local_error`.
- `quiche/src/lib.rs:4970`: after the first `CONNECTION_CLOSE` packet is built, `draining_timer` is set immediately.
- `quiche/src/lib.rs:7782`: `is_draining()` checks only `draining_timer.is_some()`.
- `quiche/src/lib.rs:3964`: `send_on_path()` returns `Error::Done` when `is_draining()` is true.
- `quiche/src/lib.rs:2969` and `quiche/src/lib.rs:2863`: `recv_single()` can return `Done` early, while outer `recv()` can still return `Ok(len)` for the datagram.

```rust
if push_frame_to_pkt!(b, frames, frame, left) {
    let pto = path.recovery.pto();
    self.draining_timer = Some(now + (pto * 3));
}
```

```rust
pub fn is_draining(&self) -> bool {
    self.draining_timer.is_some()
}
```

```rust
if self.is_closed() || self.is_draining() {
    return Err(Error::Done);
}
```

## Implementation Behavior

- The first local `CONNECTION_CLOSE` is emitted successfully.
- Immediately after that send, the send path behaves as draining.
- Later packets attributed to the same connection do not trigger follow-up `CONNECTION_CLOSE` frames; from the API perspective, `recv()` consumes the packet and `send()` keeps returning `Done`.

## Runtime Evidence

I ran:

```powershell
cargo test -p quiche --features internal --test closing_state_response_recheck -- --nocapture
```

The test observed:

```text
close_flight_packets=1
recv_late=Ok(38)
send_after_late=Err(Done)
server_peer_error=is_app:false code:4660 reason_len:21
```

This shows that the initial close flight was emitted, a later packet was still accepted as belonging to the connection, and the following send attempt produced `Err(Done)` instead of another `CONNECTION_CLOSE`.

## Inconsistency Reason

RFC 9000 requires an endpoint that sends `CONNECTION_CLOSE` to enter `closing` and continue sending `CONNECTION_CLOSE` in response to later attributed packets. `quiche` uses `draining_timer` immediately after the first close packet and therefore behaves too early as `draining`, where no more packets are sent.

## Impact

If the first `CONNECTION_CLOSE` is lost, the peer can send later packets and still never receive a retransmitted close signal.

## Fix Direction

Separate local `closing` state from `draining`. Enter `draining` only after receiving a peer `CONNECTION_CLOSE`; while locally closing, keep the minimal state needed to retransmit the same `CONNECTION_CLOSE` within rate and amplification limits.
