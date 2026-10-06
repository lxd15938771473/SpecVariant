# PUSH_PROMISE does not reject a request-content indication

## Summary

With default configuration, the nghttp2 client accepts a promised GET request containing `content-length: 1` and does not reset the promised stream. Runtime evidence used `1.70.90`.

## Standard Requirement

Official standards: [RFC 9113 Section 8.4](https://www.rfc-editor.org/rfc/rfc9113.html#section-8.4) and [RFC 9110 Section 8.6](https://www.rfc-editor.org/rfc/rfc9110.html#section-8.6).

RFC 9113 states that promised requests cannot include any content or a trailer section. The same section requires a client that receives a promised request indicating content to generate a `PROTOCOL_ERROR` stream error on the promised stream.

Under RFC 9110, `content-length: 1` in this request context indicates request content length. The field belongs to the request headers inside `PUSH_PROMISE`; it is not a HEAD/304 response exception, and no actual request DATA frame is needed to trigger the RFC 9113 rule.

## Relevant Source Code

The following source paths are relative to `implementions/nghttp2-master/nghttp2-master`.

`lib/nghttp2_http.c:440-443` routes `PUSH_PROMISE` to request-header validation. The `content-length` handling around `lib/nghttp2_http.c:278-286` checks only syntax and duplication:

```c
case NGHTTP2_TOKEN_CONTENT_LENGTH: {
  if (stream->content_length != -1) {
    return NGHTTP2_ERR_HTTP_HEADER;
  }
  stream->content_length = parse_uint(nv->value->base, nv->value->len);
  if (stream->content_length == -1) {
    return NGHTTP2_ERR_HTTP_HEADER;
  }
  break;
}
```

`lib/nghttp2_http.c:476-483` clears the state at the end of the header block without rejecting a positive content length:

```c
if (frame->hd.type == NGHTTP2_PUSH_PROMISE) {
  stream->http_flags &= NGHTTP2_HTTP_FLAG_METH_ALL;
  stream->content_length = -1;
}
return 0;
```

`lib/nghttp2_session.c:3755-3798` calls the final validation and only invokes `session_handle_invalid_stream2()` when validation returns nonzero. That reset path exists, but positive `content-length` on a promised request does not reach it.

## Implementation Behavior

`content-length: 1` is parsed as `1`, then reset to `-1`; the `PUSH_PROMISE` is delivered to the frame-received callback normally. The test did not disable HTTP messaging validation and did not ignore an error through a callback.

## Inconsistency Reason

RFC 9113 requires rejecting promised requests that indicate request content. nghttp2 rejects malformed `content-length` syntax but misses the semantic rule that `PUSH_PROMISE` cannot indicate request content.

## Runtime Evidence

A focused in-memory client/server probe was compiled and run. It completed the HTTP/2 preface and SETTINGS exchange, then sent `PUSH_PROMISE` on request stream 1 with promised stream 2. The promised request pseudo-headers were `GET`, `https`, `example.test`, and `/content-probe`; the cases varied only the `Content-Length` value. The probe drained the client's send queue and checked whether the promised stream was reset.

Observed output:

```text
nghttp2_version=1.70.90
case=GET-no-content-length summary client_push=1 client_errors=0 server_rst=0 server_protocol_rst=0
case=GET-content-length-1 summary client_push=1 client_errors=0 server_rst=0 server_protocol_rst=0
server recv_rst stream=2 error=1
case=GET-content-length-invalid summary client_push=0 client_errors=1 server_rst=1 server_protocol_rst=1
```

The valid control without `Content-Length` was accepted. The invalid promised request with `content-length: 1` was also accepted. The invalid syntax case `content-length: x` correctly triggered `RST_STREAM(PROTOCOL_ERROR)` on stream 2, proving that the harness did capture client reset behavior when nghttp2 generated it. Exit code `0` means the probe completed its checks; it does not mean the implementation is compliant.

## Impact

The default receive path delivers a prohibited promised request to the application. The evidence confirms a protocol-compliance defect; it does not establish cache poisoning or another directly exploitable security impact.

## Fix Direction

Before clearing `content_length` at the end of `PUSH_PROMISE` request-header validation, reject positive content lengths and reuse the existing promised-stream `PROTOCOL_ERROR` path. This report does not classify `content-length: 0` as the same violation.
