# `:authority` accepts deprecated userinfo

## Standard

[RFC 9113](https://www.rfc-editor.org/rfc/rfc9113.html) forbids `userinfo` in `:authority` for `http` and `https`:

[RFC 9113 Section 8.3.1](https://www.rfc-editor.org/rfc/rfc9113.html#section-8.3.1):

```text
":authority" MUST NOT include the deprecated userinfo subcomponent
for "http" or "https" schemed URIs.
```

So an HTTP/2 request with `:scheme = https` and `:authority = user@example.com` must not be generated or accepted as valid.

## Code

HTTP/1.x conversion strips userinfo before setting `:authority`, so this path is compliant:

```java
// codec-http2/src/main/java/io/netty/handler/codec/http2/HttpConversionUtil.java:748-760
static void setHttp2Authority(String authority, Http2Headers out) {
    // The authority MUST NOT include the deprecated "userinfo" subcomponent
    if (authority != null) {
        if (authority.isEmpty()) {
            out.authority(EMPTY_STRING);
        } else {
            int start = authority.indexOf('@') + 1;
            ...
            out.authority(new AsciiString(authority, start, length));
        }
    }
}
```

But already-formed HTTP/2 headers are not parsed for userinfo. `DefaultHttp2Headers` only rejects duplicate pseudo-headers and empty pseudo-header values:

```java
// codec-http2/src/main/java/io/netty/handler/codec/http2/DefaultHttp2Headers.java:171-192
if (nameValidator() == HTTP2_NAME_VALIDATOR && forAdd && hasPseudoHeaderFormat(name)) {
    if (contains(name)) {
        PlatformDependent.throwException(connectionError(
                PROTOCOL_ERROR, "Duplicate HTTP/2 pseudo-header '%s' encountered.", name));
    }
}
...
if (nameValidator() == HTTP2_NAME_VALIDATOR && (value == null || value.length() == 0) &&
        hasPseudoHeaderFormat(name)) {
    PlatformDependent.throwException(connectionError(
            PROTOCOL_ERROR, "HTTP/2 pseudo-header '%s' must not be empty.", name));
}
```

HPACK validation checks pseudo-header ordering/type and connection-specific headers, but not `:authority` userinfo:

```java
// codec-http2/src/main/java/io/netty/handler/codec/http2/HpackDecoder.java:385-408
if (hasPseudoHeaderFormat(name)) {
    ...
    return currentHeaderType;
}
if (HttpHeaderValidationUtil.isConnectionHeader(name, true)) {
    throw streamError(streamId, PROTOCOL_ERROR, ...);
}
if (HttpHeaderValidationUtil.isTeNotTrailers(name, value)) {
    throw streamError(streamId, PROTOCOL_ERROR, ...);
}
return HeaderType.REGULAR_HEADER;
```

The encoder also writes every supplied header entry:

```java
// codec-http2/src/main/java/io/netty/handler/codec/http2/HpackEncoder.java:146-153
for (Map.Entry<CharSequence, CharSequence> header : headers) {
    CharSequence name = header.getKey();
    CharSequence value = header.getValue();
    encodeHeader(out, name, value, sensitivityDetector.isSensitive(name, value),
      HpackHeaderField.sizeOf(name, value));
}
```

## Runtime Evidence

The focused tests constructed mutable and read-only HTTP/2 headers with `:authority=user@example.com`, then HPACK-encoded and decoded them with strict validation. The userinfo remained in both cases. A conversion-helper control stripped it to `example.com`. The three focused tests and two existing controls passed.

Source linkage probe:

The source-linkage harness was executed in control and candidate/reproducer modes against the checked Netty source. This was a static evidence check; the separate Java or JUnit execution below provides the runtime observation.

All 8 runs for the four test cases exited `0` and returned `standard_evidence_verified: true`.

Focused JUnit probe:

```text
cd implementions/netty-4.2
.\mvnw.cmd -pl codec-http2 "-Dtest=io.netty.handler.codec.http2.SpecLitmusAuthorityUserinfoProbeTest,io.netty.handler.codec.http2.HttpConversionUtilTest#setHttp2AuthorityWithUserInfo+setHttp2AuthorityWithEmptyAuthority" "-DfailIfNoTests=false" "-Dcheckstyle.skip=true" test
```

The temporary probe verified:

- `new DefaultHttp2Headers(true, true, 4).scheme("https").authority("user@example.com")` is accepted.
- HPACK encode/decode with `DefaultHttp2HeadersDecoder(true, true)` preserves `authority() == "user@example.com"`.
- `ReadOnlyHttp2Headers.clientHeaders(true, ..., "https", "user@example.com")` is also accepted and decoded unchanged.
- `HttpConversionUtil.setHttp2Authority("user@example.com", ...)` strips it to `example.com`.

Result:

```text
SpecLitmusAuthorityUserinfoProbeTest: Tests run: 3, Failures: 0, Errors: 0, Skipped: 0
HttpConversionUtilTest: Tests run: 2, Failures: 0, Errors: 0, Skipped: 0
Results: Tests run: 5, Failures: 0, Errors: 0, Skipped: 0
BUILD SUCCESS
```

## Decision

This behavior occurs in the low-level HTTP/2 headers/HPACK path. Netty correctly strips userinfo during HTTP/1.x conversion, but strict HTTP/2 header construction and strict HPACK decoding can still accept and preserve `:authority = user@example.com` with `:scheme = https`, which [RFC 9113](https://www.rfc-editor.org/rfc/rfc9113.html) forbids.
