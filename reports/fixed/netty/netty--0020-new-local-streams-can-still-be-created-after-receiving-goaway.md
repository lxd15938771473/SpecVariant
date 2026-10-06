# New local streams can still be created after receiving GOAWAY

## Summary

- Standard: [RFC 9113 Section 6.8](https://www.rfc-editor.org/rfc/rfc9113.html#section-6.8)
- Issue: after receiving GOAWAY, Netty rejects new streams only when `streamId > Last-Stream-ID`; a new stream at or below `Last-Stream-ID` can still be created.

## Standard Requirement

Official standard: [RFC 9113 Section 6.8](https://www.rfc-editor.org/rfc/rfc9113.html#section-6.8)

[RFC 9113 Section 6.8](https://www.rfc-editor.org/rfc/rfc9113.html#section-6.8)

```text
Receivers of a GOAWAY frame MUST NOT open additional streams on the connection,
although a new connection can be established for new streams.
```

`Last-Stream-ID` identifies the highest peer-initiated stream the GOAWAY sender might have processed. It does not authorize the recipient to create additional streams at or below that ID.

## Relevant Source Code

`codec-http2/src/main/java/io/netty/handler/codec/http2/DefaultHttp2ConnectionDecoder.java:262-265`

```java
void onGoAwayRead0(ChannelHandlerContext ctx, int lastStreamId, long errorCode, ByteBuf debugData)
        throws Http2Exception {
    listener.onGoAwayRead(ctx, lastStreamId, errorCode, debugData);
    connection.goAwayReceived(lastStreamId, errorCode, debugData);
}
```

On receiving GOAWAY, the connection records `lastStreamId`.

`codec-http2/src/main/java/io/netty/handler/codec/http2/DefaultHttp2Connection.java:226-242`

```java
public void goAwayReceived(final int lastKnownStream, long errorCode, ByteBuf debugData) throws Http2Exception {
    if (localEndpoint.lastStreamKnownByPeer() >= 0 && localEndpoint.lastStreamKnownByPeer() < lastKnownStream) {
        throw connectionError(PROTOCOL_ERROR, "lastStreamId MUST NOT increase. Current value: %d new value: %d",
                localEndpoint.lastStreamKnownByPeer(), lastKnownStream);
    }

    localEndpoint.lastStreamKnownByPeer(lastKnownStream);
    ...
    closeStreamsGreaterThanLastKnownStreamId(lastKnownStream, localEndpoint);
}
```

`codec-http2/src/main/java/io/netty/handler/codec/http2/DefaultHttp2Connection.java:776-786`

```java
public DefaultStream createStream(int streamId, boolean halfClosed) throws Http2Exception {
    State state = activeState(streamId, IDLE, isLocal(), halfClosed);

    checkNewStreamAllowed(streamId, state);

    lastCreatedStreamIdentity++;
    DefaultStream stream = new DefaultStream(lastCreatedStreamIdentity, streamId, state);
    incrementExpectedStreamId(streamId);
```

`codec-http2/src/main/java/io/netty/handler/codec/http2/DefaultHttp2Connection.java:910-916`

```java
private void checkNewStreamAllowed(int streamId, State state) throws Http2Exception {
    assert state != IDLE;
    if (lastStreamKnownByPeer >= 0 && streamId > lastStreamKnownByPeer) {
        throw streamError(streamId, REFUSED_STREAM,
                "Cannot create stream %d greater than Last-Stream-ID %d from GOAWAY.",
                streamId, lastStreamKnownByPeer);
    }
```

This check rejects only `streamId > Last-Stream-ID`; it does not prohibit every new stream when `goAwayReceived()` is true.

`codec-http2/src/main/java/io/netty/handler/codec/http2/DefaultHttp2ConnectionEncoder.java:189-204`

```java
private ChannelFuture writeHeaders0(final ChannelHandlerContext ctx, final int streamId,
                                    final Http2Headers headers, final boolean hasPriority,
                                    final int streamDependency, final short weight,
                                    final boolean exclusive, final int padding,
                                    final boolean endOfStream, ChannelPromise promise) {
    try {
        Http2Stream stream = connection.stream(streamId);
        if (stream == null) {
            try {
                stream = connection.local().createStream(streamId, /*endOfStream*/ false);
```

An application opening a stream by writing HEADERS reaches the same `connection.local().createStream(...)` check.

`codec-http2/src/main/java/io/netty/handler/codec/http2/DefaultHttp2ConnectionEncoder.java:343-348`

```java
public ChannelFuture writePushPromise(ChannelHandlerContext ctx, int streamId, int promisedStreamId,
        Http2Headers headers, int padding, ChannelPromise promise) {
    try {
        if (connection.goAwayReceived()) {
            throw connectionError(PROTOCOL_ERROR, "Sending PUSH_PROMISE after GO_AWAY received.");
```

Netty explicitly prohibits `PUSH_PROMISE` after receiving GOAWAY, but has no equivalent guard for creating ordinary HEADERS streams.

## Implementation Behavior

After receiving GOAWAY, Netty stores `Last-Stream-ID`. `checkNewStreamAllowed` rejects a new local stream whose ID exceeds that value. If the ID is at or below it and satisfies the other ordering, direction, and concurrency checks, `createStream` still creates the stream.

## Inconsistency Reason

[RFC 9113](https://www.rfc-editor.org/rfc/rfc9113.html) prohibits a GOAWAY recipient from opening any additional streams on the connection. Netty instead prohibits only streams with IDs greater than `Last-Stream-ID`, treating the value as an upper bound for further stream creation and omitting the general prohibition.

## Runtime Evidence

The standalone probe opened local stream 1, recorded receipt of GOAWAY, and attempted to create local stream 3. With `Last-Stream-ID=7`, creation succeeded and stream 3 became `OPEN`. With `Last-Stream-ID=1`, creation failed with `REFUSED_STREAM`. The two cases isolate the ID comparison that permits a new stream after GOAWAY.

Probe: `GoAwayNewStreamProbe`.

Execution method:

The standalone Java probe was compiled against the checked Netty build and its local dependencies, then executed. The setup, inputs, and observed results are described in this section.

Output:

```text
case lastStreamId=7, firstStream=1, goAwayReceived=true, localLastKnownByPeer=7
postGoAwayCreate streamId=3 result=ALLOWED state=OPEN, lastStreamCreated=3
case lastStreamId=1, firstStream=1, goAwayReceived=true, localLastKnownByPeer=1
postGoAwayCreate streamId=3 result=REJECTED error=REFUSED_STREAM message=Cannot create stream 3 greater than Last-Stream-ID 1 from GOAWAY.
```

After receiving `GOAWAY(lastStreamId=7)`, Netty still allowed creation of `streamId=3`. It rejected creation only when the new ID exceeded `Last-Stream-ID`.

## Impact

A client or server can initiate further HEADERS streams on the same connection after receiving GOAWAY when their IDs do not exceed `Last-Stream-ID`. This conflicts with shutdown expectations and can cause the peer to ignore those later stream frames.

## Fix Direction

Reject all new local stream creation when `connection.goAwayReceived()` is true. Continue using `Last-Stream-ID` to manage streams that were already created or in flight.
