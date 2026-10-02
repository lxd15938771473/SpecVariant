# STOP_SENDING on receive-only streams is accepted
## Summary

RFC 9000 Section 19.5 requires an endpoint that receives `STOP_SENDING` for a receive-only stream to terminate the connection with `STREAM_STATE_ERROR`. After rechecking the standard, the real s2n-quic code path, and a candidate-specific runtime probe, the implementation was confirmed to return `Ok(())` instead.

## Standard Requirement

- RFC 9000 Section 19.5: [STOP_SENDING Frames](https://www.rfc-editor.org/rfc/rfc9000.html#section-19.5)
- RFC 9000 Section 2.1: [Stream Types and Identifiers](https://www.rfc-editor.org/rfc/rfc9000.html#section-2.1)
- RFC 9000 Section 3.2: [Receiving Stream States](https://www.rfc-editor.org/rfc/rfc9000.html#section-3.2)
- RFC 9000 Section 3.5: [Solicited State Transitions](https://www.rfc-editor.org/rfc/rfc9000.html#section-3.5)

RFC 9000 Section 19.5 says an endpoint that receives `STOP_SENDING` for a receive-only stream must terminate the connection with `STREAM_STATE_ERROR`.

Additional context:

- Unidirectional streams carry data only from initiator to peer.
- For a peer-initiated unidirectional stream, the local endpoint has only a receiving side.
- The Section 3.5 behavior of sending `RESET_STREAM` after receiving `STOP_SENDING` assumes the local endpoint actually has a sending state.

## Relevant Source Code

`quic/s2n-quic-transport/src/stream/stream_impl.rs:195-214`

```rust
let send_is_closed = config.stream_id.stream_type().is_unidirectional()
    && config.stream_id.initiator() != config.local_endpoint_type;
...
has_send: !send_is_closed,
send_stream: SendStream::new(..., send_is_closed, ...)
```

For a peer-initiated unidirectional stream, `has_send` is `false`. From the local endpoint's perspective, the stream is receive-only.

`quic/s2n-quic-transport/src/stream/stream_impl.rs:253-277`

```rust
if !self.has_send {
    return Err(transport::Error::STREAM_STATE_ERROR
        .with_reason("MAX_STREAM_DATA sent on receive-only stream"));
}
...
fn on_stop_sending(...) -> Result<(), transport::Error> {
    self.send_stream.on_stop_sending(frame, events)
}
```

The adjacent `MAX_STREAM_DATA` requirement has a direction check. `STOP_SENDING` does not; it is forwarded directly to `send_stream`.

`quic/s2n-quic-transport/src/stream/send_stream.rs:475-479,953-962`

```rust
let data_sender = if is_closed {
    DataSender::new_finished(flow_controller, max_buffer_capacity)
} else {
    DataSender::new(flow_controller, max_buffer_capacity)
};
...
if self.data_sender.state() == data_sender::State::Finished {
    return InitResetResult::ResetNotNecessary
}
```

Because the local sending side starts closed/finished, `on_stop_sending` does not produce a transport error and ultimately returns `Ok(())`.

## Runtime Evidence

I temporarily added candidate-specific probes that directly exercised the real `StreamImpl` path, then removed the probes after validation.

Positive control: the adjacent requirement errors correctly in the same scenario.

```text
cargo test -p s2n-quic-transport stream::testing::max_stream_data_on_receive_only_stream_is_stream_state_error -- --exact --nocapture

running 1 test
test stream::testing::max_stream_data_on_receive_only_stream_is_stream_state_error ... ok
```

Reproducer for this requirement:

```text
cargo test -p s2n-quic-transport stream::testing::stop_sending_on_receive_only_stream_is_stream_state_error -- --exact --nocapture

running 1 test
test stream::testing::stop_sending_on_receive_only_stream_is_stream_state_error ... FAILED

thread 'stream::testing::stop_sending_on_receive_only_stream_is_stream_state_error' panicked:
called `Result::unwrap_err()` on an `Ok` value: ()
```

This shows that the implementation accepted `STOP_SENDING` for a receive-only stream instead of closing with `STREAM_STATE_ERROR`.

## Inconsistency Reason

The standard requires a connection error. The implementation forwards the frame to a sending side that is already finished, receives `ResetNotNecessary`, and returns `Ok(())`. That directly conflicts with RFC 9000 Section 19.5.

## Conclusion

The runtime evidence supports keeping this as a standards violation.
