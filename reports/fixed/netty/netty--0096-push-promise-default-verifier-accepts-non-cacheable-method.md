# PUSH_PROMISE default verifier accepts non-cacheable method

## Summary

Netty can enforce PUSH_PROMISE method cacheability through `Http2PromisedRequestVerifier`, but its default verifier treats every promised request as cacheable. A client using the default decoder accepts `:method=OPTIONS`, although OPTIONS is safe but not cacheable.

## Standard Requirement

- [RFC 9113 Section 8.4](https://www.rfc-editor.org/rfc/rfc9113.html#section-8.4) / §8.4.1: promised requests and `PUSH_PROMISE` `:method` must be safe and cacheable; invalid promised requests require `PROTOCOL_ERROR` on the promised stream.
- [RFC 9110 Section 9.2.1](https://www.rfc-editor.org/rfc/rfc9110.html#section-9.2.1): `OPTIONS` is safe.
- [RFC 9110 Section 9.2.3](https://www.rfc-editor.org/rfc/rfc9110.html#section-9.2.3) and §9.3.7: cache semantics are defined for `GET`, `HEAD`, and `POST`; OPTIONS responses are not cacheable.

Interpretation: `:method=OPTIONS` isolates the cacheability requirement because it is safe but non-cacheable.

## Relevant Source Code

`implementions/netty-4.2/codec-http2/src/main/java/io/netty/handler/codec/http2/Http2PromisedRequestVerifier.java:52-70`

```java
52:     /**
53:      * A default implementation of {@link Http2PromisedRequestVerifier} which always returns positive responses for
54:      * all verification challenges.
55:      */
56:     Http2PromisedRequestVerifier ALWAYS_VERIFY = new Http2PromisedRequestVerifier() {
57:         @Override
58:         public boolean isAuthoritative(ChannelHandlerContext ctx, Http2Headers headers) {
59:             return true;
60:         }
61: 
62:         @Override
63:         public boolean isCacheable(Http2Headers headers) {
64:             return true;
65:         }
66: 
67:         @Override
68:         public boolean isSafe(Http2Headers headers) {
69:             return true;
70:         }
```

`ALWAYS_VERIFY.isCacheable()` always returns `true`.

`implementions/netty-4.2/codec-http2/src/main/java/io/netty/handler/codec/http2/AbstractHttp2ConnectionHandlerBuilder.java:104-108`

```java
104:     private SensitivityDetector headerSensitivityDetector;
105:     private Boolean encoderEnforceMaxConcurrentStreams;
106:     private Boolean encoderIgnoreMaxHeaderListSize;
107:     private Http2PromisedRequestVerifier promisedRequestVerifier = ALWAYS_VERIFY;
108:     private boolean autoAckSettingsFrame = true;
```

`implementions/netty-4.2/codec-http2/src/main/java/io/netty/handler/codec/http2/DefaultHttp2ConnectionDecoder.java:70-80`

```java
70:     public DefaultHttp2ConnectionDecoder(Http2Connection connection,
71:                                          Http2ConnectionEncoder encoder,
72:                                          Http2FrameReader frameReader) {
73:         this(connection, encoder, frameReader, ALWAYS_VERIFY);
74:     }
75: 
76:     public DefaultHttp2ConnectionDecoder(Http2Connection connection,
77:                                          Http2ConnectionEncoder encoder,
78:                                          Http2FrameReader frameReader,
79:                                          Http2PromisedRequestVerifier requestVerifier) {
80:         this(connection, encoder, frameReader, requestVerifier, true);
```

The builder and default decoder constructor use `ALWAYS_VERIFY` unless a stricter verifier is configured. `Http2FrameCodecBuilder` and `Http2MultiplexCodecBuilder` pass this verifier into the decoder:

```java
246:             Http2ConnectionDecoder decoder = new DefaultHttp2ConnectionDecoder(connection, encoder, frameReader,
247:                     promisedRequestVerifier(), isAutoAckSettingsFrame(), isAutoAckPingFrame(), isValidateHeaders(),
248:                     isValidateRequiredPseudoHeaders());
261:             Http2ConnectionDecoder decoder = new DefaultHttp2ConnectionDecoder(connection, encoder, frameReader,
262:                     promisedRequestVerifier(), isAutoAckSettingsFrame(), isAutoAckPingFrame(), isValidateHeaders(),
263:                     isValidateRequiredPseudoHeaders());
```

`implementions/netty-4.2/codec-http2/src/main/java/io/netty/handler/codec/http2/DefaultHttp2ConnectionDecoder.java:653-672`

```java
653:             if (!requestVerifier.isAuthoritative(ctx, headers)) {
654:                 throw streamError(promisedStreamId, PROTOCOL_ERROR,
655:                         "Promised request on stream %d for promised stream %d is not authoritative",
656:                         streamId, promisedStreamId);
657:             }
658:             if (!requestVerifier.isCacheable(headers)) {
659:                 throw streamError(promisedStreamId, PROTOCOL_ERROR,
660:                         "Promised request on stream %d for promised stream %d is not known to be cacheable",
661:                         streamId, promisedStreamId);
662:             }
663:             if (!requestVerifier.isSafe(headers)) {
664:                 throw streamError(promisedStreamId, PROTOCOL_ERROR,
665:                         "Promised request on stream %d for promised stream %d is not known to be safe",
666:                         streamId, promisedStreamId);
667:             }
668: 
669:             // Reserve the push stream based with a priority based on the current stream's priority.
670:             connection.remote().reservePushStream(promisedStreamId, parentStream);
671: 
672:             listener.onPushPromiseRead(ctx, streamId, promisedStreamId, headers, padding);
```

`onPushPromiseRead()` rejects non-cacheable promised requests only if `requestVerifier.isCacheable(headers)` returns `false`; otherwise it reserves the promised stream and calls the listener.

## Implementation Behavior

With default settings, Netty does not derive cacheability from `:method`. Therefore OPTIONS passes the default check even though [RFC 9113](https://www.rfc-editor.org/rfc/rfc9113.html) requires the promised method to be cacheable. A custom verifier can reject it, but default client behavior remains permissive.

## Inconsistency Reason

The standard requires the client to reject a non-cacheable promised request with `PROTOCOL_ERROR`. Netty's default verifier reports every promised request as cacheable, so the default decoder accepts a safe-but-non-cacheable OPTIONS promise.

## Runtime Evidence

Two temporary client-decoder tests delivered PUSH_PROMISE with `:method=OPTIONS`, which isolates the cacheability requirement. The default verifier allowed reservation and listener delivery. A custom verifier returning `isCacheable=false` produced `PROTOCOL_ERROR` and prevented reservation. Both tests passed.

Focused tests were temporarily added to `DefaultHttp2ConnectionDecoderTest`, executed, snapshotted, and then removed from the source tree.

Command:

```powershell
.\mvnw.cmd -pl codec-http2 -Dtest=io.netty.handler.codec.http2.DefaultHttp2ConnectionDecoderTest#specLitmusDefaultVerifierAcceptsNonCacheablePromisedOptionsMethod+specLitmusCustomVerifierRejectsNonCacheablePromisedOptionsMethod -Dsurefire.failIfNoSpecifiedTests=false -DskipITs -Dcheckstyle.skip=true -DskipHttp2Testsuite=true test
```

Observed behavior:

- default verifier + `:method=OPTIONS`: accepted; `reservePushStream` and listener callback occurred;
- custom verifier with `isCacheable=false`: rejected with `PROTOCOL_ERROR`; no stream reservation occurred.

Result:

```text
[INFO] Tests run: 2, Failures: 0, Errors: 0, Skipped: 0, Time elapsed: 0.511 s -- in io.netty.handler.codec.http2.DefaultHttp2ConnectionDecoderTest
[INFO] Tests run: 2, Failures: 0, Errors: 0, Skipped: 0
[INFO] BUILD SUCCESS
[INFO] Finished at: 2026-09-10T09:33:24+08:00
```

## Impact

A default Netty HTTP/2 client can accept server push for a method that is not cacheable. This violates the PUSH_PROMISE validation requirement and can expose applications to invalid pushed responses unless they install a strict custom verifier.

## Fix Direction

Make the default promised-request verifier reject methods that are not known to be both safe and cacheable, or add method-aware cacheability validation in `onPushPromiseRead()` before reserving the promised stream.
