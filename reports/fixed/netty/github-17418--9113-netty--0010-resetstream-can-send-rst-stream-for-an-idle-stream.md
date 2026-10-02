# resetStream can send RST_STREAM for an idle stream

## Standard

[RFC 9113 Section 6.4](https://www.rfc-editor.org/rfc/rfc9113.html#section-6.4):

```text
RST_STREAM frames MUST be associated with a stream.  If a RST_STREAM
frame is received with a stream identifier of 0x00, the recipient
MUST treat this as a connection error (Section 5.4.1) of type
PROTOCOL_ERROR.

RST_STREAM frames MUST NOT be sent for a stream in the "idle" state.
If a RST_STREAM frame identifying an idle stream is received, the
recipient MUST treat this as a connection error (Section 5.4.1) of
type PROTOCOL_ERROR.
```

## Code

`Http2ConnectionHandler.java:824-832` sends any unknown stream id to `resetUnknownStream(...)` when `connection().stream(streamId)` returns `null`:

```java
public ChannelFuture resetStream(final ChannelHandlerContext ctx, int streamId, long errorCode,
                                 ChannelPromise promise) {
    final Http2Stream stream = connection().stream(streamId);
    if (stream == null) {
        return resetUnknownStream(ctx, streamId, errorCode, promise.unvoid());
    }
    return resetStream(ctx, stream, errorCode, promise);
}
```

`Http2ConnectionHandler.java:813-821` then writes `RST_STREAM` directly:

```java
private ChannelFuture resetUnknownStream(final ChannelHandlerContext ctx, int streamId, long errorCode,
                                         ChannelPromise promise) {
    ChannelFuture future = frameWriter().writeRstStream(ctx, streamId, errorCode, promise);
    ...
    return future;
}
```

The known-stream path has an idle guard, but the unknown-stream path does not. `Http2ConnectionHandler.java:849-857`:

```java
if (stream.state() == IDLE ||
    connection().local().created(stream) && !stream.isHeadersSent() && !stream.isPushPromiseSent()) {
    future = promise.setSuccess();
} else {
    future = frameWriter().writeRstStream(ctx, stream.id(), errorCode, promise);
}
```

`DefaultHttp2Connection.java:191-197` exposes `streamMayHaveExisted(streamId)`, and `DefaultHttp2Connection.java:766-767` defines it as `isValidStreamId(streamId) && streamId <= lastStreamCreated()`. A fresh connection has not created stream `13`, so stream `13` is idle.

## Runtime evidence

The JUnit probe used a fresh client-side `DefaultHttp2Connection(false)` and requested `resetStream(ctx, 13, STREAM_CLOSED, promise)` before stream 13 had been opened. It verified a call to `frameWriter.writeRstStream` for stream 13. The test passed, showing that this handler API forwards a reset for an idle stream ID.

Probe: `SpecLitmusIdleRstStreamSendProbeTest`.

Command:

```text
cd implementions/netty-4.2
mvnw.cmd -pl codec-http2 -Dtest=SpecLitmusIdleRstStreamSendProbeTest -Dsurefire.failIfNoSpecifiedTests=false -DskipITs=true -DskipHttp2Tests=false -Dcheckstyle.skip=true -DskipJapicmp=true -DskipNativeTests=true test
```

Observed behavior: with a fresh `DefaultHttp2Connection(false)`, calling `resetStream(ctx, 13, STREAM_CLOSED, promise)` reaches `frameWriter.writeRstStream(ctx, 13, STREAM_CLOSED, promise)`. Maven reported `Tests run: 1, Failures: 0, Errors: 0, Skipped: 0` and `BUILD SUCCESS`.

## Impact and fix

`resetStream(ctx, streamId, ...)` can make Netty emit a frame that a conforming peer must treat as a connection-level `PROTOCOL_ERROR`. It should avoid writing `RST_STREAM` when `stream == null` and `!connection().streamMayHaveExisted(streamId)`.
