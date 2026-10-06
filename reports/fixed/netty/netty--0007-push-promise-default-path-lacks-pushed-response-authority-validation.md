# PUSH_PROMISE default path lacks pushed-response authority validation

## Summary

[RFC 9113](https://www.rfc-editor.org/rfc/rfc9113.html) requires a client receiving a pushed response to validate server authority or proxy configuration. Netty has a verifier hook for this, but the default verifier always approves authority. With default settings, a PUSH_PROMISE for another authority is accepted and the pushed response HEADERS on the promised stream are accepted too.

## Standard Requirement

- Official standard: [RFC 9113 Section 8.4.2](https://www.rfc-editor.org/rfc/rfc9113.html#section-8.4.2)
- Section: [RFC 9113 Section 8.4.2](https://www.rfc-editor.org/rfc/rfc9113.html#section-8.4.2), Push Responses

```text
2878:    Clients receiving a pushed response MUST validate that either the
2879:    server is authoritative (see Section 10.1) or the proxy that provided
2880:    the pushed response is configured for the corresponding request.  For
2881:    example, a server that offers a certificate for only the example.com
2882:    DNS-ID (see [RFC6125]) is not permitted to push a response for
2883:    <https://www.example.org/doc>.
```

For a direct server connection, this means the client must verify that the server is authoritative for the pushed response authority before accepting the pushed response.

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

`ALWAYS_VERIFY.isAuthoritative()` always returns `true`.

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

The default builder/decoder path uses `ALWAYS_VERIFY`. Frame-codec builders pass that verifier into the decoder:

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

The decoder rejects only if `requestVerifier.isAuthoritative(ctx, headers)` returns `false`; otherwise it reserves the promised stream.

`implementions/netty-4.2/codec-http2/src/main/java/io/netty/handler/codec/http2/DefaultHttp2ConnectionDecoder.java:444-447,501-505`

```java
444:             switch (stream.state()) {
445:                 case RESERVED_REMOTE:
446:                     stream.open(endOfStream);
447:                     break;
...
501:             stream.headersReceived(isInformational);
502:             verifyContentLength(stream, 0, endOfStream);
503:             encoder.flowController().updateDependencyTree(streamId, streamDependency, weight, exclusive);
504:             listener.onHeadersRead(ctx, streamId, headers, streamDependency,
505:                     weight, exclusive, padding, endOfStream);
```

After the promised stream exists, response HEADERS on `RESERVED_REMOTE` are opened and delivered to the listener.

## Implementation Behavior

Default Netty clients do not derive authority from TLS/certificates, proxy configuration, or the `:authority` value in the default verifier. A custom verifier can enforce the rule, but the default path accepts the promised authority and then accepts the pushed response HEADERS.

## Inconsistency Reason

The standard says the client MUST validate authority for a pushed response. Netty's default path treats every pushed request as authoritative, so acceptance does not prove either required condition: authoritative server or configured proxy.

## Runtime Evidence

Two temporary client-decoder tests used PUSH_PROMISE for `GET https://www.example.org/doc`. The default authority verifier accepted the promise and subsequent pushed-response HEADERS. With `isAuthoritative=false`, the same promise caused `PROTOCOL_ERROR` without stream reservation. Both tests passed. This is a verifier-path test; it does not perform a TLS certificate or proxy-configuration validation exercise.

Temporary focused tests were added to `DefaultHttp2ConnectionDecoderTest`, run, snapshotted, and removed.

Command:

```powershell
.\mvnw.cmd -pl codec-http2 -Dtest=io.netty.handler.codec.http2.DefaultHttp2ConnectionDecoderTest#specLitmusDefaultVerifierAcceptsNonAuthoritativePushedResponse+specLitmusCustomVerifierRejectsNonAuthoritativePushPromise -Dsurefire.failIfNoSpecifiedTests=false -DskipITs -Dcheckstyle.skip=true -DskipHttp2Testsuite=true test
```

Observed behavior:

- default verifier accepted `PUSH_PROMISE` with `:authority=www.example.org`, reserved the promised stream, then accepted response `HEADERS` on that stream;
- custom verifier with `isAuthoritative=false` rejected the same `PUSH_PROMISE` with `PROTOCOL_ERROR` and did not reserve the stream.

Result:

```text
[INFO] Tests run: 2, Failures: 0, Errors: 0, Skipped: 0, Time elapsed: 0.981 s -- in io.netty.handler.codec.http2.DefaultHttp2ConnectionDecoderTest
[INFO] Tests run: 2, Failures: 0, Errors: 0, Skipped: 0
[INFO] BUILD SUCCESS
[INFO] Finished at: 2026-09-10T10:02:13+08:00
```

## Impact

A default Netty HTTP/2 client can accept pushed responses for authorities that were not actually validated, unless the application installs a strict custom verifier.

## Fix Direction

Make the default verifier perform real authority validation, or require an explicit verifier before accepting server push for authorities outside the validated connection/proxy configuration.
