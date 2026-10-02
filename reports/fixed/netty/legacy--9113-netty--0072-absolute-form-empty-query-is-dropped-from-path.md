# Absolute-form empty query is dropped from `:path`

## Problem Description

[RFC 9113](https://www.rfc-editor.org/rfc/rfc9113.html) requires `:path` to contain the target URI path and, when present, `?` plus the query. Netty preserves ordinary queries, origin-form empty queries, and asterisk form, but drops the `?` for absolute-form targets with an empty query.

## Standard Requirement

Official standards: [RFC 9113 Section 8.3.1](https://www.rfc-editor.org/rfc/rfc9113.html#section-8.3.1), [RFC 9110 Section 4.2.1](https://www.rfc-editor.org/rfc/rfc9110.html#section-4.2.1), [RFC 3986 Section 3.4](https://www.rfc-editor.org/rfc/rfc3986.html#section-3.4)

Source: [RFC 9113 Section 8.3.1](https://www.rfc-editor.org/rfc/rfc9113.html#section-8.3.1)

```text
*  The ":path" pseudo-header field includes the path and query parts
   of the target URI (the absolute-path production and, optionally, a
   '?' character followed by the query production; see Section 4.1 of
   [HTTP]).  A request in asterisk form (for OPTIONS) includes the
   value '*' for the ":path" pseudo-header field.
```

Source: [RFC 9113 Section 8.3.1](https://www.rfc-editor.org/rfc/rfc9113.html#section-8.3.1)

```text
This pseudo-header field MUST NOT be empty for "http" or "https"
URIs; "http" or "https" URIs that do not contain a path component
MUST include a value of '/'.
```

[RFC 9110](https://www.rfc-editor.org/rfc/rfc9110.html) defines `http-URI = "http" "://" authority path-abempty [ "?" query ]`, and [RFC 3986](https://www.rfc-editor.org/rfc/rfc3986.html) defines `query = *( pchar / "/" / "?" )`, so the query can be present and empty. Therefore `http://example.com/a/b?` should produce `:path=/a/b?`; `http://example.com?` should produce `:path=/?` because [RFC 9113](https://www.rfc-editor.org/rfc/rfc9113.html) also requires `/` when an `http` URI has no path component.

## Relevant Source Code

`codec-http2/src/main/java/io/netty/handler/codec/http2/HttpConversionUtil.java:611-620`

```java
private static AsciiString toHttp2Path(String uri) {
    String path = dropEmptyFragment(parsePath(uri));
    String query = parseQuery(uri);
    if (isNullOrEmpty(query)) {
        return path.isEmpty() ? EMPTY_REQUEST_PATH : new AsciiString(path);
    }
    StringBuilder pathBuilder = new StringBuilder(path.length() + query.length() + 1);
    pathBuilder.append(path);
    appendQuery(pathBuilder, query);
    return new AsciiString(pathBuilder.toString());
}
```

`parseQuery` returns an empty string for a trailing `?`, but `isNullOrEmpty(query)` treats it as absent.

`codec-http2/src/main/java/io/netty/handler/codec/http2/HttpConversionUtil.java:626-669`

```java
private static String parsePath(String uri) {
    if (uri.isEmpty()) {
        return StringUtil.EMPTY_STRING;
    }
    int i;
    if (uri.charAt(0) == '/') {
        i = 0;
    } else {
        i = uri.indexOf("://");
        if (!isValidScheme(uri, i)) {
            i = 0;
        } else {
            int authorityStart = i + 3;
            int queryOrFragmentStart = queryOrFragmentStart(uri, authorityStart);
            i = uri.indexOf('/', authorityStart);
            if (i == -1 || (queryOrFragmentStart != -1 && queryOrFragmentStart < i)) {
                return "/";
            }
        }
    }
    int queryStart = uri.indexOf('?', i);
    if (queryStart == -1) {
        queryStart = uri.length();
        if (i == 0) {
            return uri;
        }
    }
    return uri.substring(i, queryStart);
}

private static String parseQuery(String uri) {
    int i = uri.indexOf('?');
    if (i == -1) {
        return null;
    } else {
        return uri.substring(i + 1);
    }
}
```

For `http://example.com/a/b?`, `parsePath` returns `/a/b` and `parseQuery` returns `""`; `toHttp2Path` emits `/a/b`. For `http://example.com?`, `parsePath` returns `/`, `parseQuery` returns `""`; `toHttp2Path` emits `/`.

Existing test coverage also encodes this behavior:

`codec-http2/src/test/java/io/netty/handler/codec/http2/HttpConversionUtilTest.java:336-350`

```java
HttpRequest emptyQuery = new DefaultHttpRequest(
        HttpVersion.HTTP_1_1, HttpMethod.GET, "http://example.com/path?", true);

assertEquals(new AsciiString("/path"), HttpConversionUtil.toHttp2Headers(emptyQuery, true).path());
```

## Runtime Evidence

The focused tests converted request-targets covering origin form, asterisk form, absolute URIs with empty or nonempty queries, and an encoded hash in a query. Absolute URIs ending in an empty query lost the trailing `?`: `/a/b?` became `/a/b`, and `/?` became `/`. The other listed cases preserved the expected path. All six tests passed while asserting the recorded behavior.

Command, run from `implementions/netty-4.2`:

```text
.\mvnw.cmd -pl codec-http2 '-Dtest=SpecLitmusPathPseudoHeaderProbeTest' '-Dsurefire.failIfNoSpecifiedTests=false' '-DskipITs=true' '-DskipHttp2Tests=false' '-DskipNativeTests=true' '-Dcheckstyle.skip=true' '-DskipJapicmp=true' test
```

Result:

```text
Tests run: 6, Failures: 0, Errors: 0, Skipped: 0
BUILD SUCCESS
```

Observed output:

```text
case=origin-empty-query expected=/a/b? actual=/a/b?
case=asterisk-form expected=* actual=*
case=absolute-empty-query-after-path expected=/a/b? actual=/a/b
case=absolute-non-empty-query expected=/a/b?x=1&next=/home?ok=true actual=/a/b?x=1&next=/home?ok=true
case=encoded-hash-query expected=/a/b?q=a%23b actual=/a/b?q=a%23b
case=absolute-empty-query-after-empty-path expected=/? actual=/
```

## Inconsistency Reason

The standard requires `:path` to include the path and query parts of the target URI. Netty treats an empty query in absolute-form as no query, so it omits the `?` delimiter. That changes `http://example.com/a/b?` from expected `:path=/a/b?` to actual `:path=/a/b`, and `http://example.com?` from expected `:path=/?` to actual `:path=/`.

## Remaining Uncertainty

None for the outbound absolute-form empty-query path tested here.
