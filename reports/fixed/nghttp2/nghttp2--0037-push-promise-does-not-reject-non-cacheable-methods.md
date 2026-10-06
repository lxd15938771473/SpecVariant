# PUSH_PROMISE does not reject non-cacheable methods

## Summary

nghttp2 accepts a received `PUSH_PROMISE` whose promised request uses `:method: OPTIONS`. `OPTIONS` is safe, but not a method whose responses are defined as cacheable by RFC 9110. RFC 9113 requires the client to reset such a promised stream with `PROTOCOL_ERROR`; the runtime probe shows no reset is sent.

## Standard Requirement

RFC 9113 says promised requests must be safe and cacheable, and a client receiving a promised request that is not cacheable must reset the promised stream with `PROTOCOL_ERROR`:

```text
Promised requests MUST be safe ... and cacheable ...
... MUST reset the promised stream ... PROTOCOL_ERROR.
```

Relevant sections:

- [RFC 9113 Section 8.4](https://www.rfc-editor.org/rfc/rfc9113.html#section-8.4): promised requests are required to be safe and cacheable; non-cacheable promised requests require a promised-stream `PROTOCOL_ERROR`.
- [RFC 9113 Section 8.4.1](https://www.rfc-editor.org/rfc/rfc9113.html#section-8.4.1): the `PUSH_PROMISE` field block must be a complete request and `:method` must be safe and cacheable.
- [RFC 9110 Sections 9.2.1](https://www.rfc-editor.org/rfc/rfc9110.html#section-9.2.1), [9.2.3](https://www.rfc-editor.org/rfc/rfc9110.html#section-9.2.3), and [9.3.7](https://www.rfc-editor.org/rfc/rfc9110.html#section-9.3.7): `OPTIONS` is a safe method, but the methods with generally defined cacheable responses are `GET`, `HEAD`, and `POST`; `OPTIONS` responses are not cacheable.

## Code Evidence

`lib/nghttp2_submit.c:223-285` queues caller-supplied promised headers without checking whether `:method` is cacheable:

```c
rv = nghttp2_nv_array_copy(&nva_copy, nva, nvlen, mem);
...
nghttp2_frame_push_promise_init(&frame->push_promise, flags_copy, stream_id,
                                promised_stream_id, nva_copy, nvlen);
...
return promised_stream_id;
```

`lib/nghttp2_http.c:199-228` recognizes `OPTIONS` and marks it as a method flag; only `CONNECT` is rejected for pushed streams:

```c
if (lstreq("CONNECT", nv->value->base, nv->value->len)) {
  if (stream->stream_id % 2 == 0) {
    return NGHTTP2_ERR_HTTP_HEADER;
  }
  stream->http_flags |= NGHTTP2_HTTP_FLAG_METH_CONNECT;
}
...
if (lstreq("OPTIONS", nv->value->base, nv->value->len)) {
  stream->http_flags |= NGHTTP2_HTTP_FLAG_METH_OPTIONS;
}
```

`lib/nghttp2_http.c:449-483` validates request completeness and path shape, then accepts `PUSH_PROMISE` request headers. There is no safe/cacheable method check:

```c
if ((stream->http_flags & NGHTTP2_HTTP_FLAG_REQ_HEADERS) !=
      NGHTTP2_HTTP_FLAG_REQ_HEADERS ||
    (stream->http_flags &
     (NGHTTP2_HTTP_FLAG__AUTHORITY | NGHTTP2_HTTP_FLAG_HOST)) == 0) {
  return -1;
}
...
if (frame->hd.type == NGHTTP2_PUSH_PROMISE) {
  stream->http_flags &= NGHTTP2_HTTP_FLAG_METH_ALL;
  stream->content_length = -1;
}
return 0;
```

`lib/nghttp2_session.c:3755-3798` would map a request-header validation failure on `PUSH_PROMISE` to the promised stream, but `OPTIONS` does not fail validation:

```c
rv = nghttp2_http_on_request_headers(subject_stream, frame);
...
if (frame->hd.type == NGHTTP2_PUSH_PROMISE) {
  stream_id = frame->push_promise.promised_stream_id;
}
rv = session_handle_invalid_stream2(session, stream_id, frame,
                                    NGHTTP2_ERR_HTTP_MESSAGING);
```

Application paths do not disprove the issue: `src/HttpServer.cc:969-1005` and `src/shrpx_http2_upstream.cc:2265-2301` both generate built-in pushes with `:method: GET`. The confirmed gap is for received PUSH_PROMISE validation and for callers that submit non-cacheable promised methods through the public API.

## Runtime Evidence

The focused probe was rebuilt against the current static library and executed with two promised-request methods: `GET` as the positive control and `OPTIONS` as the non-cacheable-method reproducer.

Key output:

```text
case=GET push_submit=2
client recv_push stream=1 promised=2
case=GET summary client_push=1 client_errors=0 server_rst=0 server_protocol_rst=0

case=OPTIONS push_submit=2
client push_header stream=1 promised=2 :method=OPTIONS
client recv_push stream=1 promised=2
case=OPTIONS summary client_push=1 client_errors=0 server_rst=0 server_protocol_rst=0
```

Expected behavior for `OPTIONS`: client resets promised stream with `PROTOCOL_ERROR`.

Observed behavior: client accepts the push and the server receives no `RST_STREAM`; `server_protocol_rst=0`.

## Impact

A peer can send a non-cacheable promised request such as `OPTIONS`, and nghttp2 will accept it instead of enforcing RFC 9113's promised-stream `PROTOCOL_ERROR`. Intermediary code may then process or forward a push that should have been rejected.

## Fix Direction

Add PUSH_PROMISE method validation for receive-side HTTP messaging enforcement. At minimum, reject known non-cacheable promised methods such as `OPTIONS` on the promised stream with `PROTOCOL_ERROR`; the submit API or documented helper layer should also prevent generating known non-cacheable promised methods.
