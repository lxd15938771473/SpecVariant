# CONNECTION_CLOSE does not close stream-facing API state

## Summary

## Merged Redundant Reports

The following report had the same implementation cause and the same required fix, so it has been merged into this report and the redundant file was removed:

- `501-1000/0012-streams-not-immediately-closed-on-close.md`

The shared cause is that receiving peer `CONNECTION_CLOSE` moves the connection toward draining/closed transport state but does not close stream-facing API state. The shared fix is to reject stream API use once the connection is draining or closed and make existing stream state reflect connection termination.

`quiche` enters draining when it receives `ConnectionClose` or `ApplicationClose`, but it does not close existing streams or reject stream-facing API use. Buffered reads still succeed, writes on existing streams still return `Ok`, and new local streams can still be opened through `stream_send()`.

## Standard Requirement

RFC 9000 Section 10.2.1:

```text
An endpoint's selected connection ID and the QUIC version are
sufficient information to identify packets for a closing connection;
the endpoint MAY discard all other connection state.
```

RFC 9000 Section 10.2.2:

```text
While otherwise identical to the closing state, an
endpoint in the draining state MUST NOT send any packets.
```

RFC 9000 Section 19.19:

```text
If there are open streams that have not been explicitly closed, they
are implicitly closed when the connection is closed.
```

Interpretation: once close is processed, stream-facing state must no longer expose streams as open, readable, or writable.

## Relevant Source Code

`quiche/src/lib.rs:8781-8803`

```rust
frame::Frame::ConnectionClose { error_code, reason, .. } => {
    self.peer_error = Some(ConnectionError {
        is_app: false,
        error_code,
        reason,
    });

    let path = self.paths.get_active()?;
    self.draining_timer = Some(now + (path.recovery.pto() * 3));
},

frame::Frame::ApplicationClose { error_code, reason } => {
    self.peer_error = Some(ConnectionError {
        is_app: true,
        error_code,
        reason,
    });

    let path = self.paths.get_active()?;
    self.draining_timer = Some(now + (path.recovery.pto() * 3));
},
```

Close frames only set peer error and enter draining.

`quiche/src/lib.rs:5767-5784`, `quiche/src/lib.rs:5954-6005`, `quiche/src/lib.rs:6560-6578`

```rust
fn do_stream_recv<B: bytes::BufMut>(
    &mut self, stream_id: u64, action: RecvAction<B>,
) -> Result<(usize, bool)> {
    let stream = self.streams.get_mut(stream_id)
        .ok_or(Error::InvalidStreamState(stream_id))?;

    if !stream.is_readable() {
        return Err(Error::Done);
    }
```

```rust
fn stream_do_send<B, R, SND>(
    &mut self, stream_id: u64, buf: B, fin: bool, write_fn: SND,
) -> Result<R> {
    let stream = match self.get_or_create_stream(stream_id, true) {
        Ok(v) => v,
        Err(e) => return Err(e),
    };
```

```rust
pub fn stream_closed(&self, stream_id: u64) -> bool {
    let Some(stream) = self.streams.get(stream_id) else {
        return self.streams.is_collected(stream_id);
    };

    match (stream.bidi, stream.local) {
        (true, _) => stream.recv.is_fin() && stream.send.is_fin(),
        (false, true) => stream.send.is_fin(),
        (false, false) => stream.recv.is_fin(),
    }
}
```

These stream APIs do not check `is_draining()` or `is_closed()`, and `stream_closed()` only reflects FIN/reset/collected state.

`quiche/src/lib.rs:2969-2970`, `quiche/src/lib.rs:3964-3965`, `quiche/src/lib.rs:4109-4110`

```rust
if self.is_closed() || self.is_draining() {
    return Err(Error::Done);
}

if self.is_draining() {
    return Err(Error::Done);
}
```

Packet receive/send is blocked in draining, so the mismatch is specific to stream-facing state and API behavior.

## Runtime Evidence

Probe source:

- `../runtime/0006-connection-close-stream-api-state-probe/Cargo.toml`
- `../runtime/0006-connection-close-stream-api-state-probe/src/main.rs`

Command from workspace root:

```powershell
$env:CARGO_TARGET_DIR='implementions/quiche/target'
cargo run --quiet --manifest-path 'opt/runs/rfc9000/rfc9000-quiche/1501-2000/runtime/0006-connection-close-stream-api-state-probe/Cargo.toml'
```

Key output:

```text
#### case=connection_close
is_draining=true
stream_closed=false
new_stream_send_after_close(stream 1)=Ok(5)
stream_recv_after_close=Ok((5, false))
stream_send_after_close=Ok(5)
send_after_close=Err(Done)

#### case=application_close
is_draining=true
stream_closed=false
new_stream_send_after_close(stream 1)=Ok(5)
stream_recv_after_close=Ok((5, false))
stream_send_after_close=Ok(5)
send_after_close=Err(Done)
```

After either close frame variant is processed, packet transmission stops, but stream API still exposes old streams as open and even allows opening a new local stream.

## Inconsistency Reason

RFC 9000 requires open streams to be implicitly closed when the connection is closed. `quiche` only transitions the connection into draining; it does not make stream-facing state reflect closure. Because a new local stream can still be created after close, this is not just stale buffered data.

## Fix Direction

- Reject `stream_send()`, `stream_recv()`, `stream_writable()`, `readable()`, and related stream-state queries once the connection is draining or closed.
- When processing `ConnectionClose` or `ApplicationClose`, mark existing streams as implicitly closed and clear readable/writable queues so stream-facing state matches connection closure.
