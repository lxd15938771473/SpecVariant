# `validateHeaders(false)` accepts uppercase HTTP/2 field names

## Analysis
Reproduction condition: only reproduced when HTTP/2 header validation is disabled through `Http2FrameCodecBuilder.validateHeaders(false)`.

## Standard

[RFC 9113](https://www.rfc-editor.org/rfc/rfc9113.html) defines uppercase field names as malformed:

[RFC 9113 Section 8.1.1](https://www.rfc-editor.org/rfc/rfc9113.html#section-8.1.1):

```text
A malformed request or response is one that is an otherwise valid
sequence of HTTP/2 frames but is invalid due to ... the inclusion of uppercase
field names ...
```

Detected malformed messages must become stream errors:

[RFC 9113 Section 8.1.1](https://www.rfc-editor.org/rfc/rfc9113.html#section-8.1.1):

```text
Malformed requests or responses that are detected MUST be treated as a stream error ...
of type PROTOCOL_ERROR.
```

## Code

Netty has a validator that rejects uppercase names:

```java
// implementions/netty-4.2/codec-http2/src/main/java/io/netty/handler/codec/http2/DefaultHttp2Headers.java:66-90
index = ((AsciiString) name).forEachByte(HTTP2_NAME_VALIDATOR_PROCESSOR);
if (index != -1) {
    PlatformDependent.throwException(connectionError(PROTOCOL_ERROR,
            "invalid header name [%s]", name));
}
...
if (isUpperCase(name.charAt(i))) {
    PlatformDependent.throwException(connectionError(PROTOCOL_ERROR,
            "invalid header name [%s]", name));
}
```

But `DefaultHttp2Headers(false)` replaces that validator with `NameValidator.NOT_NULL`:

```java
// DefaultHttp2Headers.java:123-128
super(CASE_SENSITIVE_HASHER,
      CharSequenceValueConverter.INSTANCE,
      validate ? HTTP2_NAME_VALIDATOR : NameValidator.NOT_NULL);
```

Inbound HPACK decoding uses the same configurable flag for both storage and semantic validation:

```java
// DefaultHttp2HeadersDecoder.java:155-159, 208-209
final Http2Headers headers = newHeaders();
hpackDecoder.decode(streamId, headerBlock, headers, validateHeaders);
return new DefaultHttp2Headers(validateHeaders, validateHeaderValues, ...);
```

```java
// HpackDecoder.java:558-562
headers.add(name, value);
if (validateHeaders) {
    previousType = validateHeader(streamId, name, value, previousType);
}
```

The flag is exposed by the HTTP/2 builder and defaults to true:

```java
// AbstractHttp2ConnectionHandlerBuilder.java:286-297
protected boolean isValidateHeaders() {
    return validateHeaders != null ? validateHeaders : true;
}
protected B validateHeaders(boolean validateHeaders) {
    this.validateHeaders = validateHeaders;
    return self();
}
```

## Runtime evidence

The focused tests sent a request HEADERS block containing `Foo: bar` through frame-codec pipelines with `validateHeaders(true)` and `validateHeaders(false)`. Enabled validation suppressed upstream delivery and emitted `RST_STREAM(PROTOCOL_ERROR)` on stream 3. Disabled validation delivered the uppercase field unchanged and emitted no reset. Both tests passed.

Probe: `SpecLitmusUppercaseHeaderValidationProbeTest`.

Command:

```text
.\mvnw.cmd -pl codec-http2 -Dtest=SpecLitmusUppercaseHeaderValidationProbeTest -Dsurefire.failIfNoSpecifiedTests=false -DskipITs=true -DskipHttp2Tests=false -DskipNativeTests=true -Dcheckstyle.skip=true -DskipJapicmp=true test
```

Result:

```text
Tests run: 2, Failures: 0, Errors: 0, Skipped: 0
BUILD SUCCESS
```

Probe observations:

- `validateHeaders(true)`: a request HEADERS block containing `Foo: bar` was not delivered upstream; Netty emitted `RST_STREAM` with `PROTOCOL_ERROR` for stream 3.
- `validateHeaders(false)`: the same HEADERS block was delivered upstream as `Http2HeadersFrame`, `frame.headers().get("Foo") == "bar"`, and no `RST_STREAM` was emitted.

## Conclusion

This is a real configuration-dependent compliance issue. [RFC 9113](https://www.rfc-editor.org/rfc/rfc9113.html) makes uppercase field names malformed, but Netty can be configured to accept and deliver them. `validateHeaders(false)` should not disable the mandatory lowercase field-name check.
