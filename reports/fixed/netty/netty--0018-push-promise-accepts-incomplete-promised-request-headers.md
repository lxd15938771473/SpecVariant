# PUSH_PROMISE accepts incomplete promised request headers

## Standard

[RFC 9113](https://www.rfc-editor.org/rfc/rfc9113.html) separates frame format from promised-request semantics:

- [RFC 9113 Section 4.3](https://www.rfc-editor.org/rfc/rfc9113.html#section-4.3), lines 539-548: a field block carries all field lines of one field section; header sections include control data as pseudo-header fields; field blocks carry control data and header sections for promised requests.
- [RFC 9113 Section 6.6](https://www.rfc-editor.org/rfc/rfc9113.html#section-6.6), lines 1803-1804: a PUSH_PROMISE Field Block Fragment contains request control data and a header section.
- [RFC 9113 Section 8.4.1](https://www.rfc-editor.org/rfc/rfc9113.html#section-8.4.1), lines 2800-2818: a PUSH_PROMISE includes a complete set of request header fields; the fields in PUSH_PROMISE/CONTINUATION frames `MUST` be valid and complete request header fields; `:method` `MUST` be safe and cacheable; if a client receives an incomplete/invalid promised request, it `MUST` respond on the promised stream with `PROTOCOL_ERROR`.

## Code

The frame-level field block handling is implemented:

- `codec-http2/src/main/java/io/netty/handler/codec/http2/DefaultHttp2FrameReader.java:550-577` reads padding, `promisedStreamId`, reassembles PUSH_PROMISE/CONTINUATION fragments, decodes `Http2Headers`, then calls `listener.onPushPromiseRead(...)`.
- `codec-http2/src/main/java/io/netty/handler/codec/http2/DefaultHttp2FrameWriter.java:371-404` HPACK-encodes headers, writes `promisedStreamId`, writes the first fragment, padding, and continuation frames.

The missing part is promised-request validation:

- `codec-http2/src/main/java/io/netty/handler/codec/http2/DefaultHttp2ConnectionDecoder.java:628-672` rejects client push and wrong parent stream state, then only calls `requestVerifier.isAuthoritative/isCacheable/isSafe`, reserves the promised stream, and forwards the event. It does not check that PUSH_PROMISE headers are a complete request header set.
- `codec-http2/src/main/java/io/netty/handler/codec/http2/Http2PromisedRequestVerifier.java:56-70` defines default `ALWAYS_VERIFY`; all three checks return `true`.
- `codec-http2/src/main/java/io/netty/handler/codec/http2/AbstractHttp2ConnectionHandlerBuilder.java:107` uses `ALWAYS_VERIFY` by default.
- `codec-http2/src/main/java/io/netty/handler/codec/http2/DefaultHttp2ConnectionDecoder.java:467-471` applies `validateRequiredPseudoHeaders` only to normal `HEADERS`, not to `PUSH_PROMISE`.
- `codec-http2/src/test/java/io/netty/handler/codec/http2/DefaultHttp2ConnectionDecoderTest.java:853-859` shows the current expected behavior: `onPushPromiseRead(..., EmptyHttp2Headers.INSTANCE, ...)` succeeds, reserves the promised stream, and notifies the listener.

## Runtime Evidence

Existing PUSH_PROMISE roundtrip and decoder tests were run first. A temporary focused decoder test then enabled `validateRequiredPseudoHeaders=true`, completed initial SETTINGS processing, and supplied `EmptyHttp2Headers.INSTANCE` in PUSH_PROMISE. Netty still reserved the promised stream. The three existing tests and the additional focused test all passed.

Source probe, from workspace root:

The source-linkage harness was executed in control and candidate/reproducer modes against the checked Netty source. This was a static evidence check; the separate Java or JUnit execution below provides the runtime observation.

Both exited `0`; the probe verified the RFC/source linkage and the cited Netty paths.

Existing Netty tests, from `implementions/netty-4.2`:

```text
.\mvnw.cmd -pl codec-http2 "-Dtest=io.netty.handler.codec.http2.Http2FrameRoundtripTest#pushPromiseFrameShouldMatch+continuedPushPromiseShouldMatch,io.netty.handler.codec.http2.DefaultHttp2ConnectionDecoderTest#pushPromiseReadShouldSucceed" "-DfailIfNoTests=false" "-Dcheckstyle.skip=true" test
```

Result: `Tests run: 3, Failures: 0, Errors: 0, Skipped: 0`. This confirms PUSH_PROMISE field block roundtrip works, and that the decoder accepts empty promised request headers.

Temporary focused probe, from `implementions/netty-4.2`, added and then removed:

```java
Http2ConnectionDecoder decoder = new DefaultHttp2ConnectionDecoder(connection, encoder, reader,
        ALWAYS_VERIFY, true, true, true, true);
Http2FrameListener internalListener = decode(decoder);
internalListener.onSettingsRead(ctx, new Http2Settings());
internalListener.onPushPromiseRead(ctx, STREAM_ID, PUSH_STREAM_ID, EmptyHttp2Headers.INSTANCE, 0);
verify(remote).reservePushStream(eq(PUSH_STREAM_ID), eq(stream));
```

Run:

```text
.\mvnw.cmd -pl codec-http2 "-Dtest=io.netty.handler.codec.http2.DefaultHttp2ConnectionDecoderTest#pushPromiseWithRequiredPseudoHeaderValidationStillAcceptsEmptyHeaders" "-DfailIfNoTests=false" "-Dcheckstyle.skip=true" test
```

Result: `Tests run: 1, Failures: 0, Errors: 0, Skipped: 0`. Even with `validateRequiredPseudoHeaders=true`, empty PUSH_PROMISE headers are accepted.

## Decision

This is a real issue. Netty correctly parses and writes the PUSH_PROMISE field block, but its default inbound path accepts an incomplete promised request (`EmptyHttp2Headers`) and reserves the promised stream instead of producing `PROTOCOL_ERROR` on that promised stream as [RFC 9113 Section 8.4.1](https://www.rfc-editor.org/rfc/rfc9113.html#section-8.4.1) requires.
