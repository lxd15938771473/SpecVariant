# default server accepts missing request pseudo-headers

## Standard

[RFC 9113](https://www.rfc-editor.org/rfc/rfc9113.html) requires every non-`CONNECT` request to carry exactly one valid `:method`, `:scheme`, and `:path`. For `http`/`https`, `:path` must not be empty; a URI with no path component must use `/`. Omitting any of these mandatory request pseudo-headers makes the request malformed ([RFC 9113 Section 8.3.1](https://www.rfc-editor.org/rfc/rfc9113.html#section-8.3.1)).

## Code

Generation is compliant. `HttpConversionUtil.toHttp2Headers` writes `out.path(toHttp2Path(requestTarget))`, and `parsePath` returns `/` when an absolute URI has no slash after the authority:

```java
if (i == -1 || (queryOrFragmentStart != -1 && queryOrFragmentStart < i)) {
    return "/";
}
```

Receiving-side validation exists in `DefaultHttp2ConnectionDecoder`:

```java
CharSequence path = headers.path();
if (path == null || path.length() == 0) {
    throw streamError(streamId, PROTOCOL_ERROR,
            "Request is missing mandatory :path pseudo-header field.");
}
```

But it only runs when `validateRequiredPseudoHeaders` is enabled. `AbstractHttp2ConnectionHandlerBuilder` says this [RFC 9113](https://www.rfc-editor.org/rfc/rfc9113.html) validation is disabled by default:

```java
protected boolean isValidateRequiredPseudoHeaders() {
    return validateRequiredPseudoHeaders != null ? validateRequiredPseudoHeaders : false;
}
```

Relevant code: `implementions/netty-4.2/codec-http2/src/main/java/io/netty/handler/codec/http2/HttpConversionUtil.java:434-461,607-646`, `DefaultHttp2ConnectionDecoder.java:287-314,467-471`, `AbstractHttp2ConnectionHandlerBuilder.java:300-305`.

## Runtime evidence

The focused tests checked pathless absolute-URI conversion and default versus strict server frame decoding. URI `https://example.org` converted to `:path=/`. A GET request missing `:path` was accepted and delivered by the default server codec without a reset, while `validateRequiredPseudoHeaders(true)` rejected it. The three tests passed.

Probe: `SpecLitmusPathSlashRequiredProbeTest`.

Command:

```text
cd implementions/netty-4.2; .\mvnw.cmd -pl codec-http2 '-Dtest=SpecLitmusPathSlashRequiredProbeTest' '-Dsurefire.failIfNoSpecifiedTests=false' '-DskipITs=true' '-DskipHttp2Tests=false' '-DskipNativeTests=true' '-Dcheckstyle.skip=true' '-DskipJapicmp=true' test
```

Result:

```text
Running io.netty.handler.codec.http2.SpecLitmusPathSlashRequiredProbeTest
Tests run: 3, Failures: 0, Errors: 0, Skipped: 0
BUILD SUCCESS
Finished at: 2026-09-09T22:44:53+08:00
```

Observed behavior:

- `https://example.org` is converted to `:scheme = https`, `:authority = example.org`, `:path = /`.
- A default server `Http2FrameCodec` accepts `GET` headers with `:method`, `:scheme`, `:authority`, but no `:path`; it delivers `Http2HeadersFrame` and emits no `RST_STREAM`.
- The same request is rejected when `validateRequiredPseudoHeaders(true)` is enabled.
- The same default-off validator gate also covers missing `:method` and missing `:scheme`, because all three request pseudo-header checks are inside `validateRequiredPseudoHeaders(...)`.

## Conclusion

This is a real issue on the default inbound path. Netty generates `/` correctly for no-path HTTP/HTTPS URIs, but a default HTTP/2 server codec accepts malformed non-`CONNECT` requests that omit mandatory request pseudo-headers because required pseudo-header validation is off by default.
