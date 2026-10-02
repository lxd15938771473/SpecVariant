# RFC 9113 Section 5.5: Extended CONNECT is accepted without checking negotiation

## Standard Requirement

[RFC 9113 Section 5.5](https://www.rfc-editor.org/rfc/rfc9113.html#section-5.5):

```text
An extension that changes existing protocol elements or state MUST be
negotiated before being used.
```

The same passage requires a SETTINGS-negotiated extension to be disabled by its initial setting value. [RFC 8441 Section 3](https://www.rfc-editor.org/rfc/rfc8441.html#section-3) introduces `:protocol` for Extended CONNECT and changes CONNECT semantics. A client may use the extension only after receiving `SETTINGS_ENABLE_CONNECT_PROTOCOL = 1`.

[RFC 9113](https://www.rfc-editor.org/rfc/rfc9113.html) allows unknown extension frames to be discarded without prior negotiation. Extensions that change existing protocol elements or state have a negotiation requirement. This report concerns `:protocol` and Extended CONNECT.

## Code Evidence

`codec-http2/src/main/java/io/netty/handler/codec/http2/Http2Settings.java:190-203` provides getters and setters for `SETTINGS_ENABLE_CONNECT_PROTOCOL`:

```java
public Boolean connectProtocolEnabled() {
    Long value = get(SETTINGS_ENABLE_CONNECT_PROTOCOL);
    if (value == null) {
        return null;
    }
    return TRUE.equals(value);
}

public Http2Settings connectProtocolEnabled(boolean enabled) {
    put(SETTINGS_ENABLE_CONNECT_PROTOCOL, enabled ? TRUE : FALSE);
    return this;
```

`codec-http2/src/main/java/io/netty/handler/codec/http2/DefaultHttp2ConnectionEncoder.java:81-120` applies peer settings for push, concurrent streams, header tables, windows, and frame size, but does not retain or enforce `connectProtocolEnabled()`.

`codec-http2/src/main/java/io/netty/handler/codec/http2/DefaultHttp2ConnectionDecoder.java:296-299` selects the Extended CONNECT branch when `:protocol` is present:

```java
if (HttpMethod.CONNECT.asciiName().contentEquals(method) &&
        !headers.contains(PseudoHeaderName.PROTOCOL.value())) {
```

A CONNECT request with `:protocol` bypasses the ordinary CONNECT rule that permits only `:authority`, and proceeds to the regular request checks for `:scheme` and `:path`. The code does not first verify negotiation through `SETTINGS_ENABLE_CONNECT_PROTOCOL`.

`strictDecode()` at `codec-http2/src/test/java/io/netty/handler/codec/http2/DefaultHttp2ConnectionDecoderTest.java:604-611` completes preface processing using only empty SETTINGS:

```java
DefaultHttp2ConnectionDecoder strict = new DefaultHttp2ConnectionDecoder(
        connection, encoder, reader, ALWAYS_VERIFY, true, true, true, true);
strict.lifecycleManager(lifecycleManager);
strict.frameListener(listener);
decode(strict).onSettingsRead(ctx, new Http2Settings());
```

The test at `codec-http2/src/test/java/io/netty/handler/codec/http2/DefaultHttp2ConnectionDecoderTest.java:692-699` accepts CONNECT with `:protocol` after those empty SETTINGS:

```java
Http2Headers headers = new DefaultHttp2Headers().method("CONNECT").scheme("https")
        .authority("example.org").path("/chat");
headers.add(Http2Headers.PseudoHeaderName.PROTOCOL.value(), "websocket");
strictDecode().onHeadersRead(ctx, STREAM_ID, headers, 0, false);
verify(listener).onHeadersRead(eq(ctx), eq(STREAM_ID), eq(headers), eq(0),
        eq(DEFAULT_PRIORITY_WEIGHT), eq(false), eq(0), eq(false));
```

## Runtime Evidence

The focused `extendedConnectAcceptedWhenEnabled` unit test initialized the decoder with empty peer SETTINGS and supplied CONNECT headers containing `:protocol`. Despite the test's name, the setup did not advertise `SETTINGS_ENABLE_CONNECT_PROTOCOL=1`. The decoder delivered the headers to the listener and the test passed. The earlier source-linkage harness only checked the cited source and standard passages.

The earlier source harness verified source snippets and references without executing the decoder:

The source-linkage harness was executed in control and candidate/reproducer modes against the checked Netty source. This was a static evidence check; the separate Java or JUnit execution below provides the runtime observation.

Key output:

```text
standard_evidence_verified=true
remaining_uncertainty=Attempt extension-frame use before and after SETTINGS negotiation...
```

A focused Netty unit test was executed to resolve that uncertainty:

```powershell
cd implementions/netty-4.2
.\mvnw.cmd -pl codec-http2 '-Dtest=io.netty.handler.codec.http2.DefaultHttp2ConnectionDecoderTest#extendedConnectAcceptedWhenEnabled' '-DfailIfNoTests=false' '-Dcheckstyle.skip=true' test
```

Key output:

```text
Running io.netty.handler.codec.http2.DefaultHttp2ConnectionDecoderTest
Tests run: 1, Failures: 0, Errors: 0, Skipped: 0
BUILD SUCCESS
```

The local line-ending style check was skipped; the tested HTTP/2 implementation was unchanged. After receiving only an empty `Http2Settings()`, the unit test confirmed that the decoder accepted CONNECT with `:protocol` and delivered it to the listener.

## Analysis
[RFC 9113](https://www.rfc-editor.org/rfc/rfc9113.html) and [RFC 8441](https://www.rfc-editor.org/rfc/rfc8441.html) require negotiation before using extensions such as Extended CONNECT that change protocol semantics. Netty's decoder does not check `SETTINGS_ENABLE_CONNECT_PROTOCOL`, and the executed unit test accepted `:protocol` after empty SETTINGS.
