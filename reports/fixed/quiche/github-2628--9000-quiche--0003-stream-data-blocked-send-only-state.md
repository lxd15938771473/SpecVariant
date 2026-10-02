# STREAM_DATA_BLOCKED for send-only streams is accepted

## Summary

`quiche` does not reject `STREAM_DATA_BLOCKED` on a send-only stream. RFC 9000 requires `STREAM_STATE_ERROR`, but the receive path only increments a counter and continues.

## Standard Requirement

- RFC: [RFC 9000, Section 19.13](https://www.rfc-editor.org/rfc/rfc9000.html#section-19.13)

```text
An endpoint that receives a STREAM_DATA_BLOCKED frame for a send-only
stream MUST terminate the connection with error STREAM_STATE_ERROR.
```

Why `stream_id = 3` is send-only for the server:

- RFC 9000 Section 2.1: [Stream Types and Identifiers](https://www.rfc-editor.org/rfc/rfc9000.html#section-2.1) defines odd stream IDs as server-initiated and bit `0x02` as unidirectional.
- RFC 9000 Section 2.2: [Sending and Receiving Data](https://www.rfc-editor.org/rfc/rfc9000.html#section-2.2) assigns the sending part of streams `1` and `3` to the server.
- RFC 9000 Section 2.3: [Stream Prioritization](https://www.rfc-editor.org/rfc/rfc9000.html#section-2.3) and the stream model imply that the server's receiving part is created only for peer-initiated streams such as `0` and `2`.

So `stream_id = 3` is a server-initiated unidirectional stream, which is send-only from the server's perspective.

## Relevant Source Code

### Stream direction helpers

`quiche/src/stream/mod.rs:877-883`

```rust
pub fn is_local(stream_id: u64, is_server: bool) -> bool {
    (stream_id & 0x1) == (is_server as u64)
}

pub fn is_bidi(stream_id: u64) -> bool {
    (stream_id & 0x2) == 0
}
```

For a server, `stream_id = 3` is local and unidirectional.

### Adjacent frame types already enforce direction

`quiche/src/lib.rs:8542-8546`

```rust
if !stream::is_bidi(stream_id) &&
    stream::is_local(stream_id, self.is_server)
{
    return Err(Error::InvalidStreamState(stream_id));
}
```

This rejects `STREAM` on the local unidirectional side, matching RFC 9000 Section 19.8: [STREAM Frames](https://www.rfc-editor.org/rfc/rfc9000.html#section-19.8).

`quiche/src/lib.rs:8606-8610`

```rust
if !stream::is_bidi(stream_id) &&
    !stream::is_local(stream_id, self.is_server)
{
    return Err(Error::InvalidStreamState(stream_id));
}
```

This rejects `MAX_STREAM_DATA` on a receive-only stream.

### STREAM_DATA_BLOCKED has no equivalent check

`quiche/src/lib.rs:8668-8674`

```rust
frame::Frame::DataBlocked { .. } => {
    self.data_blocked_recv_count =
        self.data_blocked_recv_count.saturating_add(1);
},

frame::Frame::StreamDataBlocked { .. } => {
    self.stream_data_blocked_recv_count =
        self.stream_data_blocked_recv_count.saturating_add(1);
},
```

The `stream_id` is ignored. No `InvalidStreamState` is raised.

### Error mapping is already correct

`quiche/src/error.rs:186-190`

```rust
Error::InvalidStreamState(..) =>
    WireErrorCode::StreamStateError as u64,
```

If the missing validation were added, quiche would already emit the RFC-required wire error.

## Implementation Behavior

`STREAM_DATA_BLOCKED` is parsed normally and reaches the receive handler. For this frame type, the handler records statistics only. Unlike `STREAM` and `MAX_STREAM_DATA`, it does not check whether the target stream is send-only for the receiving endpoint, so the connection stays open.

## Runtime Evidence

### Control

Existing negative test:

```text
cargo test tests::max_stream_data_receive_uni::cc_algorithm_name_1___cubic__ -- --exact --nocapture
```

Observed result:

```text
running 1 test
test tests::max_stream_data_receive_uni::cc_algorithm_name_1___cubic__ ... ok
test result: ok. 1 passed; 0 failed
```

This shows the test harness does surface `InvalidStreamState` for nearby, already-implemented direction checks.

### Reproducer

Focused temporary test was added, run, then removed after verification. It:

1. completed the handshake;
2. opened server-initiated unidirectional stream `3`;
3. injected `STREAM_DATA_BLOCKED { stream_id: 3, limit: 1024 }` from client to server.

Command:

```text
cargo test tests::stream_data_blocked_send_only_uni_runtime_check -- --exact --nocapture
```

Observed result:

```text
running 1 test
test tests::stream_data_blocked_send_only_uni_runtime_check ... ok
test result: ok. 1 passed; 0 failed
```

Assertions inside that runtime check confirmed:

- `pipe.send_pkt_to_server(...)` returned success, not `Err(Error::InvalidStreamState(3))`;
- `pipe.server.stream_data_blocked_recv_count` changed from `0` to `1`;
- `pipe.server.local_error()` remained `None`;
- `pipe.server.is_closed()` remained `false`;
- the response packet contained no `CONNECTION_CLOSE`.

That is the opposite of the RFC requirement in Section 19.13.

## Inconsistency Reason

The RFC requires immediate connection termination on `STREAM_DATA_BLOCKED` for a send-only stream. quiche already applies the same direction-based state validation to adjacent frame types, and already maps `InvalidStreamState` to `STREAM_STATE_ERROR`, but the `STREAM_DATA_BLOCKED` receive branch omits the check entirely and accepts the frame.

## Impact

A peer can send an invalid `STREAM_DATA_BLOCKED` on a send-only stream and quiche will treat it as benign traffic instead of a connection error. This is a protocol compliance bug and can hide peer state-machine violations.

## Fix Direction

Add the same direction validation used for `STREAM` to the `Frame::StreamDataBlocked` receive branch in `quiche/src/lib.rs`. For a local unidirectional stream, return `Err(Error::InvalidStreamState(stream_id))`. Add a regression test that injects `STREAM_DATA_BLOCKED` on stream `3` to the server and expects `InvalidStreamState(3)`.
