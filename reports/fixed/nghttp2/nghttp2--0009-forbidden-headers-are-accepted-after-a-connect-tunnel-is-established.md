# Forbidden HEADERS are accepted after a CONNECT tunnel is established

## Summary

On 2026-09-08, nghttp2 `1.70.90` was rechecked and rerun. After a successful CONNECT/200 tunnel is established, libnghttp2 accepts later `HEADERS` with `END_STREAM`; nghttpx frontend also accepts the same kind of client trailers in a real TCP tunnel. With client push enabled, libnghttp2 also accepts a `PUSH_PROMISE` associated with the connected CONNECT stream.

The earlier claim that every forbidden frame lacked a rejection path is not retained. Control cases show that DATA, WINDOW_UPDATE, PRIORITY, RST_STREAM, isolated CONTINUATION, client-to-server PUSH_PROMISE, non-2xx CONNECT, and ordinary GET trailers are not evidence for this issue.

## Standard Requirement

Official standard: [RFC 9113 Section 8.5](https://www.rfc-editor.org/rfc/rfc9113.html#section-8.5).

After a proxy successfully establishes the TCP connection and sends a 2xx response, the HTTP/2 stream carries tunnel data. RFC 9113 states that frame types other than DATA or stream-management frames (`RST_STREAM`, `WINDOW_UPDATE`, and `PRIORITY`) must not be sent on a connected stream. Receiving another frame type must be treated as a stream error under Section 5.4.2. Section 5.4.1 allows implementations to escalate a stream error to a connection error, so either `RST_STREAM` or `GOAWAY` would demonstrate rejection. Isolated CONTINUATION is governed by the separate Section 6.10 rule and is used only as a control.

## Relevant Source Code

`implementions/nghttp2-master/nghttp2-master/lib/nghttp2_session.c:3773-3786` routes later HEADERS after the final response into trailer handling:

```c
      case NGHTTP2_HCAT_HEADERS:
        if (stream->http_flags & NGHTTP2_HTTP_FLAG_EXPECT_FINAL_RESPONSE) {
          assert(!session->server);
          rv = nghttp2_http_on_response_headers(stream);
        } else {
          rv = nghttp2_http_on_trailer_headers(stream, frame);
        }
        break;
      default:
        assert(0);
      }
      if (rv == 0 && (frame->hd.flags & NGHTTP2_FLAG_END_STREAM)) {
        rv = nghttp2_http_on_remote_end_stream(stream);
      }
```

`lib/nghttp2_http.c:513-522` validates trailers by checking only `END_STREAM`; it ignores whether the stream is already a successful CONNECT tunnel:

```c
int nghttp2_http_on_trailer_headers(nghttp2_stream *stream,
                                    nghttp2_frame *frame) {
  (void)stream;

  if ((frame->hd.flags & NGHTTP2_FLAG_END_STREAM) == 0) {
    return -1;
  }

  return 0;
}
```

`lib/nghttp2_session.c:4580-4655` rejects PUSH_PROMISE for wrong role, disabled push, or invalid stream id, then validates stream state and opens the promised stream. It does not reject the case where the associated stream is already a connected CONNECT tunnel.

`implementions/nghttp2-master/nghttp2-master/src/shrpx_http2_upstream.cc:225-229` stores `NGHTTP2_HCAT_HEADERS` as trailers, and `src/shrpx_http2_upstream.cc:575-587` treats `END_STREAM` as normal request completion. There is no guard that rejects trailers after a CONNECT tunnel is connected.

## Implementation Behavior

On a CONNECT/200 stream, trailers with `END_STREAM` follow the ordinary HTTP trailer path and close the remote side normally. They are delivered through callbacks and do not trigger invalid-frame handling, `RST_STREAM`, or `GOAWAY`. Removing `END_STREAM` from the same header block is rejected, proving that parsing and error paths are active.

For `PUSH_PROMISE`, libnghttp2 applies generic push checks but does not check whether the associated stream is a connected CONNECT stream. This is confirmed only at library level. nghttpx backend HTTP/2 sessions send `ENABLE_PUSH=0` when `no_server_push` or `http2_proxy` is active, so the runtime nghttpx conclusion is limited to frontend trailers.

## Inconsistency Reason

After a successful 2xx CONNECT response establishes the tunnel, RFC 9113 allows only DATA and stream-management frames on that connected stream. Trailer `HEADERS` and `PUSH_PROMISE` are outside that allowed set. The implementation reuses ordinary HTTP trailer and push validation without a connected-CONNECT-stream guard, so it accepts frames that should be stream errors.

## Runtime Evidence

A library-level runner and a full nghttpx runner were executed from the workspace root. Both completed successfully: the library runner reported `{"result": "reproduced violation", "cases": 21, "selfcheck": "PASS", "standard_quote": "MATCH"}`, and the nghttpx runner's four cases all completed.

| Target | Case | Result |
| --- | --- | --- |
| libnghttp2 | CONNECT/200 trailers to client | `accepted=1`, `invalid=0`, `RST=0/0`, `GOAWAY=0/0` |
| libnghttp2 | CONNECT/200 trailers to server | `accepted=1`, `invalid=0`, `RST=0/0`, `GOAWAY=0/0` |
| libnghttp2 | fragmented trailers | client and server accepted with no RST/GOAWAY |
| libnghttp2 | PUSH_PROMISE to client | `accepted=1`, `invalid=0`, `RST=0/0`, `GOAWAY=0/0` |
| libnghttp2 | trailers without END_STREAM | rejected with `RST_STREAM(PROTOCOL_ERROR)` |
| nghttpx | real CONNECT tunnel plus DATA echo | tunnel established; DATA echo verified |
| nghttpx | trailers and fragmented trailers | accepted with no RST/GOAWAY |
| nghttpx | trailers without END_STREAM | rejected with `RST_STREAM(PROTOCOL_ERROR)` |

The accepted `HEADERS` after CONNECT/200 demonstrate the issue. The trailers-without-END_STREAM control shows that nghttp2 can reject an invalid trailer form, so the lack of rejection is specific to the connected-tunnel rule rather than missing test plumbing.

## Impact

Applications relying on default library validation may receive HTTP/2 frames that RFC 9113 requires to be rejected after CONNECT tunnel establishment. nghttpx frontend is confirmed to accept client trailers in a real tunnel. This report does not claim memory corruption, request smuggling, data leakage, or a general high-severity security impact.

## Fix Direction

Record successful CONNECT tunnel state on the stream. When later `HEADERS` or `PUSH_PROMISE` arrive and the associated stream is connected, enter stream-error handling. Preserve existing behavior for non-2xx CONNECT responses and ordinary HTTP trailers. Regression tests should cover both directions, `END_STREAM`, fragmented header blocks, push settings, and non-2xx CONNECT.
