# Incomplete PUSH_PROMISE request headers are accepted

## Problem Description

A `PUSH_PROMISE` must carry a valid and complete request header set for the promised request. Netty can write an empty `PUSH_PROMISE` header block, and the client decoder accepts the same empty block instead of sending `PROTOCOL_ERROR` on the promised stream.

## Standard Requirement

Official standard: [RFC 9113 Section 8.4.1](https://www.rfc-editor.org/rfc/rfc9113.html#section-8.4.1)

[RFC 9113 Section 8.4.1](https://www.rfc-editor.org/rfc/rfc9113.html#section-8.4.1), [RFC 9113 Section 8.4.1](https://www.rfc-editor.org/rfc/rfc9113.html#section-8.4.1):

```text
   The PUSH_PROMISE frame includes a field block that contains control
   data and a complete set of request header fields that the server
   attributes to the request.  It is not possible to push a response to
   a request that includes message content.
```

[RFC 9113 Section 8.4.1](https://www.rfc-editor.org/rfc/rfc9113.html#section-8.4.1), [RFC 9113 Section 8.4.1](https://www.rfc-editor.org/rfc/rfc9113.html#section-8.4.1):

```text
   The header fields in PUSH_PROMISE and any subsequent CONTINUATION
   frames MUST be a valid and complete set of request header fields
   (Section 8.3.1).  The server MUST include a method in the ":method"
   pseudo-header field that is safe and cacheable.  If a client receives
   a PUSH_PROMISE that does not include a complete and valid set of
   header fields or the ":method" pseudo-header field identifies a
   method that is not safe, it MUST respond on the promised stream with
   a stream error (Section 5.4.2) of type PROTOCOL_ERROR.
```

This means the promised request must include the request pseudo-header set required by Section 8.3.1, including `:method`, `:scheme`, and `:path` unless the request is CONNECT. An incomplete set must be rejected with `PROTOCOL_ERROR` on the promised stream.

## Relevant Source Code

`implementions/netty-4.2/codec-http2/src/main/java/io/netty/handler/codec/http2/DefaultHttp2FrameWriter.java:360-404`

```java
public ChannelFuture writePushPromise(ChannelHandlerContext ctx, int streamId,
        int promisedStreamId, Http2Headers headers, int padding, ChannelPromise promise) {
    verifyStreamId(streamId, STREAM_ID);
    verifyStreamId(promisedStreamId, "Promised Stream ID");
    verifyPadding(padding);
    headerBlock = ctx.alloc().buffer();
    headersEncoder.encodeHeaders(streamId, headers, headerBlock);
    // writes PUSH_PROMISE and CONTINUATION fragments
}
```

The writer validates IDs and padding, then encodes the supplied `headers`; it does not require `:method`, `:scheme`, or `:path`.

`implementions/netty-4.2/codec-http2/src/main/java/io/netty/handler/codec/http2/DefaultHttp2ConnectionDecoder.java:628-672`

```java
public void onPushPromiseRead(ChannelHandlerContext ctx, int streamId, int promisedStreamId,
        Http2Headers headers, int padding) throws Http2Exception {
    if (!requestVerifier.isAuthoritative(ctx, headers)) { ... }
    if (!requestVerifier.isCacheable(headers)) { ... }
    if (!requestVerifier.isSafe(headers)) { ... }
    connection.remote().reservePushStream(promisedStreamId, parentStream);
    listener.onPushPromiseRead(ctx, streamId, promisedStreamId, headers, padding);
}
```

The decoder checks authority/cacheability/safety through `Http2PromisedRequestVerifier`, but the default verifier returns true. The helper that rejects missing request pseudo-headers is in `DefaultHttp2ConnectionDecoder.java:287-321`; it is called for ordinary HEADERS at `DefaultHttp2ConnectionDecoder.java:467-471`, not for `PUSH_PROMISE`.

## Runtime Evidence

Three temporary tests passed an empty PUSH_PROMISE header set through default client decoding, strict client decoding, and server encoding. Both client configurations accepted the request and called the listener. The server write completed and left the promised stream in `RESERVED_LOCAL`. The run reported three passing tests.

Probe: `SpecLitmusPushPromiseCompletenessProbe-rerun-2026-09-10-snippet`.

Command:

```text
.\mvnw.cmd -pl codec-http2 -Dtest=DefaultHttp2ConnectionDecoderTest#specLitmusRerunDefaultClientAcceptsIncompletePushPromiseHeaders+specLitmusRerunStrictClientAcceptsIncompletePushPromiseHeaders,DefaultHttp2ConnectionEncoderTest#specLitmusRerunServerWritesIncompletePushPromiseHeaders -Dsurefire.failIfNoSpecifiedTests=false -DskipITs=true -DskipHttp2Tests=false -DskipNativeTests=true -Dcheckstyle.skip=true -DskipJapicmp=true test
```

Observed output:

```text
case=rerun-default-client-empty-push-promise expected=PROTOCOL_ERROR actual=accepted listener=called
case=rerun-strict-client-empty-push-promise expected=PROTOCOL_ERROR actual=accepted listener=called
case=rerun-server-write-empty-push-promise expected=reject actual=written futureDone=true streamState=RESERVED_LOCAL
Tests run: 3, Failures: 0, Errors: 0, Skipped: 0
BUILD SUCCESS
Finished at: 2026-09-10T09:10:12+08:00
```

## Inconsistency Reason

[RFC 9113](https://www.rfc-editor.org/rfc/rfc9113.html) requires `PUSH_PROMISE` headers to be a valid and complete request header set and requires clients to reset the promised stream with `PROTOCOL_ERROR` when they are incomplete. Netty can generate an empty promised request and accept an empty promised request on the client side, including with `validateRequiredPseudoHeaders` enabled.

## Remaining Uncertainty

None for empty PUSH_PROMISE headers on the tested encoder path and default/strict client decoder paths.
