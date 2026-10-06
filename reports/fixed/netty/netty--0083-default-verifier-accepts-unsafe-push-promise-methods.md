# default verifier accepts unsafe PUSH_PROMISE methods

## Standard

[RFC 9113](https://www.rfc-editor.org/rfc/rfc9113.html) requires promised requests to be safe and cacheable, and requires clients to reset unsafe promised streams:

[RFC 9113 Section 8.4](https://www.rfc-editor.org/rfc/rfc9113.html#section-8.4):

```text
Promised requests MUST be safe ... and cacheable ...
Clients that receive a promised request that is not cacheable,
that is not known to be safe, or that indicates the presence of
request content MUST reset the promised stream with a stream error
... of type PROTOCOL_ERROR.
```

The PUSH_PROMISE section repeats the method rule:

[RFC 9113 Section 8.4.1](https://www.rfc-editor.org/rfc/rfc9113.html#section-8.4.1):

```text
The server MUST include a method in the ":method" pseudo-header
field that is safe and cacheable. If a client receives a PUSH_PROMISE
... [whose] ":method" ... identifies a method that is not safe, it
MUST respond on the promised stream with ... PROTOCOL_ERROR.
```

A promised request with `:method = POST` therefore must not be accepted by default.

## Code

The default `DefaultHttp2ConnectionDecoder` constructor uses `ALWAYS_VERIFY`:

```java
// codec-http2/src/main/java/io/netty/handler/codec/http2/DefaultHttp2ConnectionDecoder.java:70-73
public DefaultHttp2ConnectionDecoder(Http2Connection connection,
                                     Http2ConnectionEncoder encoder,
                                     Http2FrameReader frameReader) {
    this(connection, encoder, frameReader, ALWAYS_VERIFY);
}
```

The builder uses the same verifier by default and passes it to the decoder:

```java
// AbstractHttp2ConnectionHandlerBuilder.java:107,666-668
private Http2PromisedRequestVerifier promisedRequestVerifier = ALWAYS_VERIFY;

DefaultHttp2ConnectionDecoder decoder = new DefaultHttp2ConnectionDecoder(connection, encoder, reader,
    promisedRequestVerifier(), isAutoAckSettingsFrame(), isAutoAckPingFrame(), isValidateHeaders(),
    isValidateRequiredPseudoHeaders());
```

On receiving PUSH_PROMISE, the decoder produces `PROTOCOL_ERROR` only if the verifier returns false; otherwise, it reserves the promised stream and calls the listener:

```java
// DefaultHttp2ConnectionDecoder.java:658-671
if (!requestVerifier.isCacheable(headers)) {
    throw streamError(promisedStreamId, PROTOCOL_ERROR, ...);
}
if (!requestVerifier.isSafe(headers)) {
    throw streamError(promisedStreamId, PROTOCOL_ERROR, ...);
}

connection.remote().reservePushStream(promisedStreamId, parentStream);
listener.onPushPromiseRead(ctx, streamId, promisedStreamId, headers, padding);
```

The default verifier returns true for every check:

```java
// codec-http2/src/main/java/io/netty/handler/codec/http2/Http2PromisedRequestVerifier.java:52-71
Http2PromisedRequestVerifier ALWAYS_VERIFY = new Http2PromisedRequestVerifier() {
    public boolean isAuthoritative(ChannelHandlerContext ctx, Http2Headers headers) {
        return true;
    }
    public boolean isCacheable(Http2Headers headers) {
        return true;
    }
    public boolean isSafe(Http2Headers headers) {
        return true;
    }
};
```

The default configuration therefore does not derive promised-request safety from `:method`. Compliance on this path depends on an explicitly configured custom verifier.

## Runtime Evidence

The focused decoder tests delivered a PUSH_PROMISE request containing `:method=POST`, `:scheme=https`, `:authority=example.org`, and `:path=/`. The default verifier allowed stream reservation and listener delivery. A custom verifier returning `isSafe=false` rejected the input with `PROTOCOL_ERROR` before reservation. Both tests passed.

The source-linkage checks were rerun for all four candidates in both `control` and `reproducer` modes. All exited 0 and confirmed that the standard evidence matched the cited source locations:

The source-linkage harness was executed in control and candidate/reproducer modes against the checked Netty source. This was a static evidence check; the separate Java or JUnit execution below provides the runtime observation.

A temporary focused JUnit test class exercised the default client path:

```text
cd implementions/netty-4.2
.\mvnw.cmd -pl codec-http2 "-Dcheckstyle.skip=true" "-DskipTests=false" "-Dtest=SpecLitmusPushPromiseSafetyDefaultTest" test
```

Result:

```text
BUILD SUCCESS
Tests run: 2, Failures: 0, Errors: 0, Skipped: 0
```

Observed behavior:

- A PUSH_PROMISE containing `:method POST`, `:scheme https`, `:authority example.org`, and `:path /` was accepted by the default decoder, which called `reservePushStream()` and `onPushPromiseRead()`.
- The same input with a custom `isSafe=false` verifier threw `PROTOCOL_ERROR` and did not reserve the promised stream.

## Decision

The standard requires clients to reset an unsafe promised request with `PROTOCOL_ERROR`. Netty's default configuration treats every promised request as safe and cacheable, and the runtime test confirmed acceptance of POST PUSH_PROMISE through the default client receive path.
