# Host and :authority mismatch is accepted

## Summary

RFC 9113 recommends that a server treat a request as malformed when `Host` and `:authority` identify different entities. nghttp2's default HTTP messaging validation checks the syntax and presence of both fields, but it does not compare them. In runtime testing, a mismatched request was fully received and no `RST_STREAM` was sent.

## Standard Requirement

Official standard: [RFC 9113 Section 8.3.1](https://www.rfc-editor.org/rfc/rfc9113.html#section-8.3.1).

```text
The recipient of an HTTP/2 request MUST NOT use the Host
header field to determine the target URI if ":authority" is
present.
```

```text
Clients MUST NOT generate a request with a Host header field that
differs from the ":authority" pseudo-header field.  A server
SHOULD treat a request as malformed if it contains a Host header
field that identifies an entity that differs from the entity in
the ":authority" pseudo-header field.
```

The server-side malformed handling here is a `SHOULD`, not a `MUST`.

## Relevant Source Code

`implementions/nghttp2-master/nghttp2-master/lib/nghttp2_http.c:193-198` checks `:authority` syntax and uniqueness:

```c
case NGHTTP2_TOKEN__AUTHORITY:
  if (!check_authority(nv->value->base, nv->value->len) ||
      !check_pseudo_header(stream, nv, NGHTTP2_HTTP_FLAG__AUTHORITY)) {
    return NGHTTP2_ERR_HTTP_HEADER;
  }
  break;
```

`lib/nghttp2_http.c:270-277` checks `Host` syntax and uniqueness:

```c
case NGHTTP2_TOKEN_HOST:
  if (!check_authority(nv->value->base, nv->value->len)) {
    return NGHTTP2_ERR_IGN_HTTP_HEADER;
  }
  if (!check_pseudo_header(stream, nv, NGHTTP2_HTTP_FLAG_HOST)) {
    return NGHTTP2_ERR_HTTP_HEADER;
  }
  break;
```

`lib/nghttp2_http.c:449-484` validates required request fields, but it does not compare `Host` with `:authority`:

```c
if ((stream->http_flags & NGHTTP2_HTTP_FLAG_REQ_HEADERS) !=
      NGHTTP2_HTTP_FLAG_REQ_HEADERS ||
    (stream->http_flags &
     (NGHTTP2_HTTP_FLAG__AUTHORITY | NGHTTP2_HTTP_FLAG_HOST)) == 0) {
  return -1;
}
...
return 0;
```

`lib/nghttp2_session.c:3741-3788` calls this final request-header validation after the HEADERS block.

`implementions/nghttp2-master/nghttp2-master/src/shrpx_http2_upstream.cc:334-374` uses `:authority` if present and falls back to `Host` only when `:authority` is absent:

```c
auto authority = req.fs.header(http2::HD__AUTHORITY);
...
if (!authority) {
  req.no_authority = true;
  authority = req.fs.header(http2::HD_HOST);
}

if (authority) {
  req.authority = authority->value;
}
```

## Implementation Behavior

Both fields are accepted if their individual syntax is valid. The library records that at least one of `:authority` or `Host` exists, but it does not normalize and compare their values. nghttpx follows the RFC rule not to use `Host` when `:authority` exists, but the inspected path does not reject a mismatch as malformed.

## Inconsistency Reason

RFC 9113 says that a server `SHOULD` treat mismatched `Host` and `:authority` as malformed. nghttp2 accepts the request and keeps the stream open. This is a confirmed divergence from a recommendation, not a hard `MUST` violation.

## Runtime Evidence

A focused server receive-path probe was compiled and run with default HTTP messaging validation enabled. It compared a matching `Host`/`:authority` request with a mismatched request.

Matching control:

```text
matching-host-authority on_header stream=1 name=:authority value=example.com
matching-host-authority on_header stream=1 name=host value=example.com
matching-host-authority frame_recv type=1 stream=1 flags=0x05
matching-host-authority summary headers=5 frames=3 rst_stream=0 saw_authority=1 saw_host=1
```

Mismatched request:

```text
mismatched-host-authority on_header stream=1 name=:authority value=example.com
mismatched-host-authority on_header stream=1 name=host value=evil.example
mismatched-host-authority frame_recv type=1 stream=1 flags=0x05
mismatched-host-authority summary headers=5 frames=3 rst_stream=0 saw_authority=1 saw_host=1
final match_result=0 mismatch_result=0
```

The mismatch was accepted and no `RST_STREAM` was sent.

## Impact

A server using default nghttp2 validation can accept requests where `Host` and `:authority` disagree. Because nghttpx prefers `:authority`, routing is not obviously controlled by `Host` in the inspected path, but downstream applications or intermediaries that still inspect `Host` may receive conflicting authority information.

## Fix Direction

Add optional or default server-side normalization and comparison for `Host` and `:authority`. When both are present and identify different entities, treat the request as malformed, or document the behavior as an intentional departure from RFC 9113's `SHOULD`.
