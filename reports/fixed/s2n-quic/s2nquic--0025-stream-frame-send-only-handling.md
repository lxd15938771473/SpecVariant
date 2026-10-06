# RFC 9000 STREAM frame handling on send-only streams

s2n-quic accepts a peer `STREAM` frame sent to an already opened locally initiated unidirectional stream and returns `Ok(())`. RFC 9000 requires the endpoint to terminate the connection with `STREAM_STATE_ERROR`.

## Standard Requirement

- RFC 9000 Section 2.1: [Stream Types and Identifiers](https://www.rfc-editor.org/rfc/rfc9000.html#section-2.1)
- RFC 9000 Section 3.1: [Sending Stream States](https://www.rfc-editor.org/rfc/rfc9000.html#section-3.1)
- RFC 9000 Section 3.2: [Receiving Stream States](https://www.rfc-editor.org/rfc/rfc9000.html#section-3.2)
- RFC 9000 Section 19.8: [STREAM Frames](https://www.rfc-editor.org/rfc/rfc9000.html#section-19.8)

Unidirectional streams carry data only from the initiator to the peer. For a server-side, server-initiated unidirectional stream, a peer `STREAM` frame before the first local `STREAM` hits the "locally initiated stream not yet created" case; after the first local `STREAM`, it hits the "send-only stream" case. Both cases require `STREAM_STATE_ERROR`.

## Relevant Source Code

`implementions/s2n-quic/quic/s2n-quic-transport/src/stream/controller/local_initiated.rs:343-345`

```rust
/// Since locally-initiated, unidirectional streams can only send data, all of
/// these operations are no-op, as the peer will become aware of the stream once
/// the local application starts sending on it.
```

`implementions/s2n-quic/quic/s2n-quic-transport/src/stream/manager.rs:326-360`

```rust
if stream_id >= first_unopened_id {
    return Err(
        transport::Error::STREAM_STATE_ERROR.with_reason("Stream was not yet opened")
    );
}

ready!(poll_open);
self.insert_stream(first_unopened_id);
```

`implementions/s2n-quic/quic/s2n-quic-transport/src/stream/stream_impl.rs:195-203,226-231`

```rust
let receive_is_closed = config.stream_id.stream_type().is_unidirectional()
    && config.stream_id.initiator() == config.local_endpoint_type;

fn on_data(
    &mut self,
    frame: &StreamRef,
    events: &mut StreamEvents,
) -> Result<(), transport::Error> {
    self.receive_stream.on_data(frame, events)
}
```

`implementions/s2n-quic/quic/s2n-quic-transport/src/stream/receive_stream.rs:361-417`

```rust
let state = if is_closed {
    ReceiveStreamState::DataRead
} else {
    ReceiveStreamState::Receiving
};

ReceiveStreamState::DataRead => {
    // We also ignore the data in this case.
}
```

The manager rejects "not yet opened" local streams. Once the local unidirectional stream has been opened, however, the `STREAM` frame reaches a receive side that is already closed and in `DataRead`, where the data is silently ignored.

## Runtime Evidence

I temporarily added and ran a probe during the recheck, then removed it. The probe:

1. opened a server-initiated unidirectional stream locally;
2. sent one byte to confirm a `STREAM` frame was actually emitted;
3. injected a peer `STREAM` frame on that stream;
4. asserted that `STREAM_STATE_ERROR` should be returned.

Command:

```text
cargo test -p s2n-quic-transport probe_rfc9000_stream_frame_on_locally_opened_unidirectional_stream_must_error
```

Observed output:

```text
running 1 test
test stream::manager::tests::probe_rfc9000_stream_frame_on_locally_opened_unidirectional_stream_must_error ... FAILED

called `Result::unwrap_err()` on an `Ok` value: ()

test result: FAILED. 0 passed; 1 failed; 0 ignored; 0 measured; 354 filtered out
```

The expected `STREAM_STATE_ERROR` did not occur; `manager.on_data(...)` returned `Ok(())`.

## Fix Direction

Reject `STREAM` frames on locally initiated unidirectional streams in `StreamImpl::on_data` or an earlier dispatch layer. Do not allow the frame to fall through to the `ReceiveStream::DataRead` ignore branch.
