# PRIORITY stream 0 is not rejected

nghttp2 accepts a PRIORITY frame whose stream identifier is `0x00` and only sends the normal SETTINGS ACK. RFC 9113 requires this case to be a connection error of type `PROTOCOL_ERROR`.

## Standard Requirement

RFC 9113, Section 6.3, "PRIORITY":

```text
If a PRIORITY frame is received with a stream identifier of 0x00
```

The same sentence continues with `MUST respond with a connection error ... PROTOCOL_ERROR`.

Important context from the same section:

- PRIORITY is deprecated, but its frame format and error rules are still defined.
- A PRIORITY frame can be sent in any stream state, including `idle` or `closed`.
- The frame still identifies a stream, so stream identifier `0x00` is invalid.
- A PRIORITY frame length other than 5 octets is a `FRAME_SIZE_ERROR`; that is separate from the stream-zero rule.

## Relevant Source Code

`lib/nghttp2_frame.c:45-51` unpacks the stream identifier from the frame header and preserves `0`:

```c
void nghttp2_frame_unpack_frame_hd(nghttp2_frame_hd *hd, const uint8_t *buf) {
  *hd = (nghttp2_frame_hd){
    .length = nghttp2_get_uint32(&buf[0]) >> 8,
    .stream_id = nghttp2_get_uint32(&buf[5]) & NGHTTP2_STREAM_ID_MASK,
    .type = buf[3],
    .flags = buf[4],
  };
}
```

`lib/nghttp2_session.c:5529-5536` reads the header and then only applies the generic maximum-frame-size check before dispatching by type:

```c
nghttp2_frame_unpack_frame_hd(&iframe->frame.hd, iframe->sbuf.pos);
iframe->payloadleft = iframe->frame.hd.length;

DEBUGF("recv: payloadlen=%zu, type=%u, flags=0x%02x, stream_id=%d\n",
       iframe->frame.hd.length, iframe->frame.hd.type,
       iframe->frame.hd.flags, iframe->frame.hd.stream_id);

if (iframe->frame.hd.length > session->local_settings.max_frame_size) {
```

`lib/nghttp2_session.c:5703-5730` handles PRIORITY by clearing flags, checking only `length == 5`, rate-limiting, and reading the 5-byte payload:

```c
case NGHTTP2_PRIORITY:
  DEBUGF("recv: PRIORITY\n");

  iframe->frame.hd.flags = NGHTTP2_FLAG_NONE;

  if (iframe->payloadleft != NGHTTP2_PRIORITY_SPECLEN) {
    busy = 1;
    iframe->state = NGHTTP2_IB_FRAME_SIZE_ERROR;
    break;
  }

  rv = session_update_glitch_ratelim(session);
  if (rv != 0) {
    return rv;
  }

  if (iframe->state == NGHTTP2_IB_IGN_ALL) {
    return (nghttp2_ssize)inlen;
  }

  iframe->state = NGHTTP2_IB_READ_NBYTE;
  inbound_frame_set_mark(iframe, NGHTTP2_PRIORITY_SPECLEN);
```

`lib/nghttp2_session.c:6224-6227` then discards the parsed PRIORITY payload without validating `stream_id`:

```c
case NGHTTP2_PRIORITY:
  session_inbound_frame_reset(session);

  break;
```

The connection-error helper exists but this path does not call it. `lib/nghttp2_session.c:3370-3385` maps `NGHTTP2_ERR_PROTO` to `NGHTTP2_PROTOCOL_ERROR`, and `lib/nghttp2_session.c:3471-3482` terminates the session through `nghttp2_session_terminate_session_with_reason`.

## Runtime Evidence

A fresh recheck ran a focused PRIORITY-frame probe against the current nghttp2 build. The positive control sent PRIORITY on stream `1`; the reproducer sent the same valid 5-byte PRIORITY payload on stream `0`.

Positive control, `priority-idle-valid`, sends a valid PRIORITY on stream `1`:

```text
scenario=priority_idle_valid
oracle=valid PRIORITY on idle nonzero stream should not emit GOAWAY/RST_STREAM
mem_recv=47 input_len=47
frame index=1 type=4 flags=1 stream=0 length=0
outbound_summary frames=1 goaway=0 rst_stream=0
```

Reproducer, `priority-stream0`, sends the same PRIORITY payload on stream `0`:

```text
scenario=priority_stream0
oracle=RFC9113 requires GOAWAY/PROTOCOL_ERROR for stream_id 0
mem_recv=47 input_len=47
frame index=1 type=4 flags=1 stream=0 length=0
outbound_summary frames=1 goaway=0 rst_stream=0
```

`type=4, flags=1, stream=0, length=0` is only a SETTINGS ACK. No GOAWAY frame is emitted, and therefore no `PROTOCOL_ERROR` is sent.

## Inconsistency Reason

RFC 9113 allows PRIORITY on idle or closed streams, but not on stream `0`. nghttp2 treats stream `0` the same as a valid nonzero stream: it reads the 5-byte payload and resets inbound frame state. This misses the required connection error of type `PROTOCOL_ERROR`.

## Impact

A malformed peer can send a stream-zero PRIORITY frame without the mandated connection-level protocol error. The practical impact is protocol-compliance and interoperability risk; no state corruption was observed in this focused test.

## Fix Direction

In the PRIORITY receive branch, check `iframe->frame.hd.stream_id == 0` before reading the payload. If true, call the connection-error path with `NGHTTP2_ERR_PROTO` and a reason such as `PRIORITY: stream_id == 0`, producing GOAWAY with `PROTOCOL_ERROR`.
