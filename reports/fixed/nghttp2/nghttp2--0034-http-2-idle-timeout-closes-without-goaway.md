# HTTP/2 idle timeout closes without GOAWAY
- Requirement level: `SHOULD`

## Standard

RFC 9113 lines 3153-3159 says servers may terminate idle connections, but when an endpoint chooses to close the transport-layer TCP connection, it `SHOULD first send a GOAWAY` so both sides can know which frames were processed.

Thus, an HTTP/2 frontend idle-timeout close should send GOAWAY before closing TCP unless there is a reason to bypass graceful close.

## Code

`nghttp2-master/src/shrpx_config.cc:3236-3239` stores `frontend-http2-idle-timeout` in `conn.upstream.timeout.http2_idle`:

```c++
case SHRPX_OPTID_FRONTEND_HTTP2_IDLE_TIMEOUT:
  return parse_duration(opt, optarg).transform([config](auto &&r) {
    config->conn.upstream.timeout.http2_idle = r;
  });
```

`nghttp2-master/src/shrpx_http2_upstream.cc:1121-1124` arms that timer for the HTTP/2 frontend:

```c++
handler_->reset_upstream_read_timeout(
  config->conn.upstream.timeout.http2_idle);
handler_->signal_write();
```

When the timer fires, `nghttp2-master/src/shrpx_client_handler.cc:68-77` directly deletes the handler:

```c++
void timeoutcb(struct ev_loop *loop, ev_timer *w, int revents) {
  auto conn = static_cast<Connection *>(w->data);
  auto handler = static_cast<ClientHandler *>(conn->data);
  Log{INFO, handler} << "Time out";
  delete handler;
}
```

That deletion closes TCP through `nghttp2-master/src/shrpx_connection.cc:93-124`:

```c++
Connection::~Connection() { disconnect(); }
...
shutdown(fd, SHUT_WR);
close(fd);
```

GOAWAY paths exist but are not used here: graceful shutdown calls `submit_goaway()` in `nghttp2-master/src/shrpx_http2_upstream.cc:913-916`, and `nghttp2_session_terminate_session()` queues GOAWAY in `nghttp2-master/lib/nghttp2_session.c:239-247`.

## Runtime

A focused idle-timeout probe ran nghttpx 1.70.90 with a cleartext HTTP/2 frontend, an HTTP/1.1 backend, frontend frame debugging, and a one-second HTTP/2 frontend idle timeout.

```text
nghttpx --conf=empty-nghttpx.conf --frontend=127.0.0.1,<port>;no-tls --backend=127.0.0.1,<port>;;proto=http/1.1 --workers=1 --log-level=INFO --frontend-frame-debug --frontend-http2-idle-timeout=1s
```

The client sent the HTTP/2 preface and SETTINGS, acknowledged the server SETTINGS, then waited for idle close. Observed result:

```json
{
  "observed_eof": true,
  "settings_ack_sent": true,
  "frame_types": ["SETTINGS", "SETTINGS"],
  "goaway_count": 0,
  "goaway_before_close": false
}
```

The server bytes were exactly two SETTINGS frames:

```text
[hex omitted]
```

The sanitized log records `direct HTTP/2 connection`, SETTINGS/ACK exchange, then `Time out` and handler deletion. No GOAWAY frame appears before EOF.

## Decision

This is a real SHOULD-level RFC 9113 inconsistency. nghttpx chooses to close the HTTP/2 frontend TCP connection on idle timeout, but this path closes the socket without first sending GOAWAY.
