# absolute-form `Host` overrides request-target authority

## Standard

[RFC 9113 Section 8.3.1](https://www.rfc-editor.org/rfc/rfc9113.html#section-8.3.1) requires an intermediary forwarding a request over HTTP/2 to construct `:authority` from the authority information in the original request's control data when the original target URI has authority. The same paragraph says `Host` is not the sole source of this information.

[RFC 9112 Section 3.2.2](https://www.rfc-editor.org/rfc/rfc9112.html#section-3.2.2) gives the HTTP/1.1 absolute-form case: a proxy receiving an absolute-form request-target must ignore the received `Host` field and use the host information from the request-target when forwarding.

## Code

`implementions/netty-4.2/codec-http2/src/main/java/io/netty/handler/codec/http2/HttpConversionUtil.java:434-461` converts HTTP/1.x requests to HTTP/2:

```java
String host = inHeaders.getAsString(HttpHeaderNames.HOST);
...
if (hasSchemeAndAuthority(requestTarget)) {
    URI requestTargetUri = URI.create(http2PathlessRequestTarget(requestTarget));
    // Take from the request-line if HOST header was empty
    host = isNullOrEmpty(host) ? requestTargetUri.getAuthority() : host;
    setHttp2Scheme(inHeaders, requestTargetUri, out);
}
...
setHttp2Authority(host, out);
```

So for an absolute-form request-target, Netty uses the request-target authority only when `Host` is empty. If `Host` is present, `:authority` is built from `Host`.

`implementions/netty-4.2/codec-http2/src/main/java/io/netty/handler/codec/http2/HttpConversionUtil.java:747-760` writes the selected value into `:authority`:

```java
out.authority(new AsciiString(authority, start, length));
```

Existing `HttpConversionUtilTest.java:217-229` covers absolute-form without `Host`, but not a conflicting `Host` value.

## Runtime evidence

The focused conversion tests used absolute request-target `http://target.example:8080/path?q=1` with and without `Host: host-header.example:9090`. Without Host, the output authority came from the target URI; with Host, it came from the conflicting header. Origin-form and empty-authority cases were also checked. All four tests passed.

Probe: `SpecLitmusHttp1AbsoluteFormAuthorityProbeTest`.

Command:

```text
cd implementions/netty-4.2; .\mvnw.cmd -pl codec-http2 '-Dtest=SpecLitmusHttp1AbsoluteFormAuthorityProbeTest' '-Dsurefire.failIfNoSpecifiedTests=false' '-DskipITs=true' '-DskipHttp2Tests=false' '-DskipNativeTests=true' '-Dcheckstyle.skip=true' '-DskipJapicmp=true' test
```

Result:

```text
Tests run: 4, Failures: 0, Errors: 0, Skipped: 0
BUILD SUCCESS
```

Observed:

- `http://target.example:8080/path?q=1` without `Host` produces `:authority = target.example:8080`.
- The same request with `Host: host-header.example:9090` produces `:authority = host-header.example:9090`.
- Origin-form uses `Host` because its request-target has no authority.
- Empty authority with no `Host` does not generate `:authority`.

## Conclusion

For an HTTP/1.1 absolute-form request that contains authority in the request-target and a conflicting `Host`, Netty constructs HTTP/2 `:authority` from `Host`. In intermediary use, this conflicts with [RFC 9113](https://www.rfc-editor.org/rfc/rfc9113.html)'s requirement to use the original request control data and [RFC 9112](https://www.rfc-editor.org/rfc/rfc9112.html)'s absolute-form forwarding rule.

Impact: a proxy/gateway using this converter can forward a request to the `Host` authority instead of the authority in the absolute-form request-target.

Fix direction: when the request-target is absolute-form and contains authority, derive `:authority` from that request-target authority even if `Host` is present; keep using `Host` for origin-form requests.
