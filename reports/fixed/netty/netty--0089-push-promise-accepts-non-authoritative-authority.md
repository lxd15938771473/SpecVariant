# PUSH_PROMISE accepts non-authoritative :authority

## Standard Requirement

[RFC 9113 Section 8.4](https://www.rfc-editor.org/rfc/rfc9113.html#section-8.4):

> The server MUST include a value in the ":authority" pseudo-header field for which the server is authoritative. A client MUST treat a PUSH_PROMISE for which the server is not authoritative as a stream error of type PROTOCOL_ERROR.

[RFC 9113 Section 10.1](https://www.rfc-editor.org/rfc/rfc9113.html#section-10.1): HTTP/2 uses HTTP authority to determine whether a server can provide a response; `https` relies on the authenticated server identity.

When a server sends PUSH_PROMISE, its `:authority` must be within the server's authority. A client receiving a non-authoritative PUSH_PROMISE must produce promised-stream `PROTOCOL_ERROR`.

## Relevant Source Code

- `implementions/netty-4.2/codec-http2/src/main/java/io/netty/handler/codec/http2/DefaultHttp2ConnectionEncoder.java:343-356`: `writePushPromise` reserves the promised stream and passes caller-supplied headers to the frame writer without authority validation.
- `implementions/netty-4.2/codec-http2/src/main/java/io/netty/handler/codec/http2/DefaultHttp2FrameWriter.java:360-390`: the writer validates stream IDs and padding and HPACK-encodes the headers; it has no server-authority set or verifier.
- `implementions/netty-4.2/codec-http2/src/main/java/io/netty/handler/codec/http2/AbstractHttp2ConnectionHandlerBuilder.java:107`: the receiving-side `promisedRequestVerifier` defaults to `ALWAYS_VERIFY`.
- `implementions/netty-4.2/codec-http2/src/main/java/io/netty/handler/codec/http2/Http2PromisedRequestVerifier.java:53-60`: `ALWAYS_VERIFY.isAuthoritative(...)` always returns true.
- `implementions/netty-4.2/codec-http2/src/main/java/io/netty/handler/codec/http2/DefaultHttp2ConnectionDecoder.java:653-657`: the decoder produces promised-stream `PROTOCOL_ERROR` only when the verifier returns false.

## Runtime Evidence

The standalone probe used a request stream for `good.example` and a PUSH_PROMISE declaring `:authority=evil.example`. The default client delivered one push frame without a reset. A client with a rejecting authority verifier delivered no push frame and emitted one `RST_STREAM(PROTOCOL_ERROR)`. The server-write case also accepted the supplied authority. Compilation and execution exited 0.

Probe: `PushPromiseAuthorityProbe`.

The standalone Java probe was compiled against the checked Netty build and its local dependencies, then executed. The setup, inputs, and observed results are described in this section.

Recorded execution status:

Compilation exited 0 and produced no diagnostic output.
The Java process exited 0.
Runtime standard error was empty.

Output:

```text
case=default-client verifier=ALWAYS_VERIFY promisedAuthority=evil.example delivered=true pushFrameCount=1 outboundRst=count=0,protocolError=false exception=<none>
case=strict-client verifier=rejecting promisedAuthority=evil.example delivered=false pushFrameCount=0 outboundRst=count=1,protocolError=true exception=<none>
case=server-write promisedAuthority=evil.example outboundAccepted=true exception=<none>
```

## Analysis
Netty can write PUSH_PROMISE with `:authority=evil.example` on a request stream for `good.example`. Its default client also delivers that promised request without `RST_STREAM(PROTOCOL_ERROR)`. This conflicts with the [RFC 9113](https://www.rfc-editor.org/rfc/rfc9113.html) PUSH_PROMISE authority requirement.
