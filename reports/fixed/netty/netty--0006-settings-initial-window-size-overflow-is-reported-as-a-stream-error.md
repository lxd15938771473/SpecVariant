# SETTINGS_INITIAL_WINDOW_SIZE overflow is reported as a stream error

## Summary

[RFC 9113](https://www.rfc-editor.org/rfc/rfc9113.html) requires a `SETTINGS_INITIAL_WINDOW_SIZE` change that makes any flow-control window exceed the maximum size to be a connection error of type `FLOW_CONTROL_ERROR`. Netty detects the overflow, but raises `Http2Exception$StreamException`; `Http2ConnectionHandler` therefore routes it to `RST_STREAM`, not connection-level `GOAWAY`.

## Standard Requirement

Official standard: [RFC 9113 Section 6.9.2](https://www.rfc-editor.org/rfc/rfc9113.html#section-6.9.2), "Initial Flow-Control Window Size" ([RFC 9113 Section 6.9.2](https://www.rfc-editor.org/rfc/rfc9113.html#section-6.9.2))

```text
When the value of SETTINGS_INITIAL_WINDOW_SIZE changes, a receiver MUST adjust
the size of all stream flow-control windows that it maintains by the difference
between the new value and the old value.

An endpoint MUST treat a change to SETTINGS_INITIAL_WINDOW_SIZE that causes any
flow-control window to exceed the maximum size as a connection error
(Section 5.4.1) of type FLOW_CONTROL_ERROR.
```

The requirement calls for a connection error, rather than resetting only the individual stream whose window overflowed.

## Relevant Source Code

`implementions/netty-4.2/codec-http2/src/main/java/io/netty/handler/codec/http2/DefaultHttp2ConnectionEncoder.java:116`

```java
Integer initialWindowSize = settings.initialWindowSize();
if (initialWindowSize != null) {
    flowController().initialWindowSize(initialWindowSize);
}
```

An inbound `SETTINGS_INITIAL_WINDOW_SIZE` value is passed to the remote flow controller.

`implementions/netty-4.2/codec-http2/src/main/java/io/netty/handler/codec/http2/DefaultHttp2RemoteFlowController.java:636`

```java
void initialWindowSize(int newWindowSize) throws Http2Exception {
    final int delta = newWindowSize - initialWindowSize;
    initialWindowSize = newWindowSize;
    connection.forEachActiveStream(new Http2StreamVisitor() {
        @Override
        public boolean visit(Http2Stream stream) throws Http2Exception {
            state(stream).incrementStreamWindow(delta);
            return true;
        }
    });
}
```

Netty adjusts every active stream window by the SETTINGS delta.

`implementions/netty-4.2/codec-http2/src/main/java/io/netty/handler/codec/http2/DefaultHttp2RemoteFlowController.java:404`

```java
int incrementStreamWindow(int delta) throws Http2Exception {
    if (delta > 0 && Integer.MAX_VALUE - delta < window) {
        throw streamError(stream.id(), FLOW_CONTROL_ERROR,
                "Window size overflow for stream: %d", stream.id());
    }
    window += delta;
    return window;
}
```

The overflow is constructed as `streamError(stream.id(), FLOW_CONTROL_ERROR)`.

`implementions/netty-4.2/codec-http2/src/main/java/io/netty/handler/codec/http2/Http2ConnectionHandler.java:691`

```java
public void onError(ChannelHandlerContext ctx, boolean outbound, Throwable cause) {
    Http2Exception embedded = getEmbeddedHttp2Exception(cause);
    if (isStreamError(embedded)) {
        onStreamError(ctx, outbound, cause, (StreamException) embedded);
    } else if (embedded instanceof CompositeStreamException) {
        for (StreamException streamException : (CompositeStreamException) embedded) {
            onStreamError(ctx, outbound, cause, streamException);
        }
    } else {
        onConnectionError(ctx, outbound, cause, embedded);
    }
}
```

`StreamException` enters the stream-error branch.

`implementions/netty-4.2/codec-http2/src/main/java/io/netty/handler/codec/http2/Http2ConnectionHandler.java:749`

```java
protected void onStreamError(ChannelHandlerContext ctx, boolean outbound,
                             Throwable cause, StreamException http2Ex) {
    final int streamId = http2Ex.streamId();
    Http2Stream stream = connection().stream(streamId);
    if (stream == null) {
        if (!outbound || connection().local().mayHaveCreatedStream(streamId)) {
            encoder().writeRstStream(ctx, streamId, http2Ex.error().code(), ctx.newPromise());
        }
    } else {
        encoder().writeRstStream(ctx, streamId, http2Ex.error().code(), ctx.newPromise());
    }
}
```

The resulting handler action writes `RST_STREAM` rather than connection-level `GOAWAY`.

## Implementation Behavior

Netty detects a window exceeding `Integer.MAX_VALUE` and uses `FLOW_CONTROL_ERROR`, but throws it as a stream error. Under `Http2ConnectionHandler.onError()` dispatch, this does not enter `onConnectionError()` and therefore does not receive connection-error handling.

## Inconsistency Reason

[RFC 9113](https://www.rfc-editor.org/rfc/rfc9113.html) requires connection `FLOW_CONTROL_ERROR` if a change to `SETTINGS_INITIAL_WINDOW_SIZE` causes any flow-control window to exceed the maximum value.

Netty handles that condition with `streamError(stream.id(), FLOW_CONTROL_ERROR)` and the handler writes `RST_STREAM`.

The error code matches, but its scope is downgraded from connection error to stream error.

## Runtime Evidence

The standalone probe adjusted an active stream's remote flow-control window and then applied a valid `SETTINGS_INITIAL_WINDOW_SIZE` increase whose delta caused the existing window to exceed `Integer.MAX_VALUE`. It inspected the exception type and error code. Netty reported stream-level `FLOW_CONTROL_ERROR`, rather than connection-level `FLOW_CONTROL_ERROR`.

Probe: `InitialWindowOverflowProbe`.

Execution method:

The standalone Java probe was compiled against the checked Netty build and its local dependencies, then executed. The setup, inputs, and observed results are described in this section.

Observed output:

```text
baseline_no_overflow result=ok window=2147483647 is_stream_error=false
settings_delta_overflows_stream_window result=exception class=io.netty.handler.codec.http2.Http2Exception$StreamException error=FLOW_CONTROL_ERROR stream_id=3 is_stream_error=true message=Window size overflow for stream: 3
```

Key probe operations:

```java
flowController.incrementWindowSize(stream, 1);
encoder.remoteSettings(new Http2Settings().initialWindowSize(Integer.MAX_VALUE));
```

The `SETTINGS_INITIAL_WINDOW_SIZE` value itself is valid. Overflow results from adding its delta to an existing active-stream window. The output confirms that Netty throws stream-level `FLOW_CONTROL_ERROR`.

## Impact

The peer can receive an individual-stream `RST_STREAM` instead of connection-level GOAWAY and closure. This weakens the connection-error handling required by [RFC 9113](https://www.rfc-editor.org/rfc/rfc9113.html) for corrupted flow-control state.

## Fix Direction

If a `SETTINGS_INITIAL_WINDOW_SIZE` delta makes any stream window exceed the maximum, throw connection-level `FLOW_CONTROL_ERROR` so `Http2ConnectionHandler` enters `onConnectionError()`. During active-stream adjustment in `initialWindowSize()`, convert this overflow into `connectionError(FLOW_CONTROL_ERROR, ...)` rather than `streamError(...)`.
