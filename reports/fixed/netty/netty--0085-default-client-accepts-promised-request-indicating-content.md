# default client accepts promised request indicating content

## Standard

[RFC 9113 Section 8.4](https://www.rfc-editor.org/rfc/rfc9113.html#section-8.4) requires promised requests to be safe, cacheable, and free of request content or trailers. A client that receives a promised request indicating request content must reset the promised stream with `PROTOCOL_ERROR` ([RFC 9113 Section 8.4](https://www.rfc-editor.org/rfc/rfc9113.html#section-8.4); [RFC 9113 Section 8.4](https://www.rfc-editor.org/rfc/rfc9113.html#section-8.4)).

```text
Promised requests MUST be safe (see Section 9.2.1 of [HTTP]) and
cacheable (see Section 9.2.3 of [HTTP]).  Promised requests cannot
include any content or a trailer section.  Clients that receive a
promised request that is not cacheable, that is not known to be safe,
or that indicates the presence of request content MUST reset the
promised stream with a stream error (Section 5.4.2) of type
PROTOCOL_ERROR.
```

## Code

The default promised-request verifier accepts every challenge:

```java
public boolean isCacheable(Http2Headers headers) {
    return true;
}

public boolean isSafe(Http2Headers headers) {
    return true;
}
```

`DefaultHttp2ConnectionDecoder.onPushPromiseRead` only checks `isAuthoritative`, `isCacheable`, and `isSafe`; if those pass, it reserves the promised stream and notifies the listener:

```java
if (!requestVerifier.isCacheable(headers)) {
    throw streamError(promisedStreamId, PROTOCOL_ERROR, ...);
}
if (!requestVerifier.isSafe(headers)) {
    throw streamError(promisedStreamId, PROTOCOL_ERROR, ...);
}

connection.remote().reservePushStream(promisedStreamId, parentStream);
listener.onPushPromiseRead(ctx, streamId, promisedStreamId, headers, padding);
```

There is no default check here for `content-length` or another request-content indicator. The `content-length` normalization path is in normal `HEADERS` handling, not in this `PUSH_PROMISE` path.

Relevant code: `implementions/netty-4.2/codec-http2/src/main/java/io/netty/handler/codec/http2/Http2PromisedRequestVerifier.java:52-70`, `AbstractHttp2ConnectionHandlerBuilder.java:100-107`, `DefaultHttp2ConnectionDecoder.java:472-487,628-672`.

## Runtime evidence

The focused tests supplied a promised request carrying `content-length: 1` to the default verifier and to a default client frame codec. The verifier returned true; the client delivered `Http2PushPromiseFrame` and emitted no `RST_STREAM`. Both tests passed. The input declares request content through its header; the probe does not need to send a request body to observe this validation gap.

Probe: `SpecLitmusPushPromiseContentProbeTest`.

Command:

```text
cd implementions/netty-4.2; .\mvnw.cmd -pl codec-http2 '-Dtest=SpecLitmusPushPromiseContentProbeTest' '-Dsurefire.failIfNoSpecifiedTests=false' '-DskipITs=true' '-DskipHttp2Tests=false' '-DskipNativeTests=true' '-DskipJapicmp=true' compiler:testCompile surefire:test
```

Result:

```text
Running io.netty.handler.codec.http2.SpecLitmusPushPromiseContentProbeTest
Tests run: 2, Failures: 0, Errors: 0, Skipped: 0
BUILD SUCCESS
Finished at: 2026-09-10T09:26:31+08:00
```

Observed behavior:

- `Http2PromisedRequestVerifier.ALWAYS_VERIFY` returns true for a promised request carrying `content-length: 1`.
- A default client `Http2FrameCodec` receives a `PUSH_PROMISE` with `content-length: 1`, delivers `Http2PushPromiseFrame`, and emits no `RST_STREAM`.

## Conclusion

This is a real issue. [RFC 9113](https://www.rfc-editor.org/rfc/rfc9113.html) requires the client to reset a promised stream when the promised request indicates request content, but Netty's default client path accepts and delivers such a `PUSH_PROMISE`.
