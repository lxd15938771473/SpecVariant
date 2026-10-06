# nghttpx opens an extra HTTP/2 backend connection after peer stream concurrency is exhausted

The original `nghttp` batching and `--multiply` suspicion is not supported. The reproduced behavior appears in `nghttpx`: as an HTTP/2 client to a configured HTTP/2 backend, it opens another simultaneous HTTP/2 TCP connection to the same backend host and port when the existing backend session reaches the peer's `SETTINGS_MAX_CONCURRENT_STREAMS`.

## Standard Requirement

RFC 9113 §9.1 says:

```text
Clients SHOULD NOT open more than one HTTP/2 connection to a given
host and port pair, where the host is derived from a URI, a selected
alternative service [ALT-SVC], or a configured proxy.

A client can create additional connections as replacements, either to
replace connections that are near to exhausting the available stream
identifier space (Section 5.1.1), to refresh the keying material for
a TLS connection, or to replace connections that have encountered
errors (Section 5.4.1).

A client MAY open multiple connections to the same IP address and TCP
port using different Server Name Indication [TLS-EXT] values or to
provide different TLS client certificates but SHOULD avoid creating
multiple connections with the same configuration.
```

This is a `SHOULD NOT`, so a documented exception can exist. The reproduced case does not match the listed exceptions: the second connection is not a replacement, key refresh, error recovery, different SNI, or different client certificate case. It is opened only because the current backend H2 session has no free concurrent-stream slot.

## Relevant Code

`nghttp` itself uses one client/session for each adjacent same-origin batch:

```c++
// src/nghttp.cc:2195
HttpClient client{callbacks, loop, ssl_ctx};

for (auto &req : requests) {
  for (int i = 0; i < config.multiply; ++i) {
    client.add_request(std::get<0>(req), std::get<1>(req), std::get<2>(req),
                       std::get<3>(req));
  }
}
...
client.resolve_host(host, port);
client.initiate_connection();
```

`nghttpx` allows several active backend downstreams by default:

```c++
// src/shrpx.cc:1847
downstreamconf.connections_per_host = 8;
```

For HTTP/2 backend sessions, it skips a reusable session when the peer stream limit is reached:

```c++
// src/shrpx_client_handler.cc:746
for (auto session = addr->http2_extra_freelist.head; session;) {
  auto next = session->dlnext;

  if (session->max_concurrency_reached(0)) {
    session->remove_from_freelist();
    session = next;
    continue;
  }
  return session;
}

auto session = new Http2Session(conn_.loop, worker_->get_cl_ssl_ctx(),
                                worker_, group, addr);
session->add_to_extra_freelist();
return session;
```

The limit check is based on remote `SETTINGS_MAX_CONCURRENT_STREAMS`:

```c++
// src/shrpx_http2_session.cc:2321
bool Http2Session::max_concurrency_reached(size_t extra) const {
  if (!session_) {
    return dconns_.size() + extra >= 100;
  }

  return !nghttp2_session_check_request_allowed(session_) ||
         dconns_.size() + extra >=
           nghttp2_session_get_remote_settings(
             session_, NGHTTP2_SETTINGS_MAX_CONCURRENT_STREAMS);
}
```

So when the only existing backend session is full, `nghttpx` creates a new backend `Http2Session` instead of queueing the request on the same H2 connection.

## Runtime Evidence

The tests were rerun after this review. Artifacts:

- `../recheck/0029-connection-management/runtime/control-results.json`
- `../recheck/0029-connection-management/runtime/variants-results.json`
- `../recheck/0029-connection-management/runtime/stream-limit-results.json`
- `../recheck/0029-connection-management/runtime/nghttpx-control-results.json`
- `../recheck/0029-connection-management/runtime/nghttpx-variants-results.json`
- `../recheck/0029-connection-management/runtime/verification.json`

`nghttp` controls:

| Case | Observed backend connections |
| --- | --- |
| 3 same-origin URIs | 1 connection, 3 streams |
| `--multiply=20` over 3 same-origin URIs | 1 connection, 60 streams |
| `--multiply=20 --peer-max-concurrent-streams=1` | 1 connection, 60 streams |
| A/B/A input order | A used 2 cumulative connections, but peak active A connection was 1 |
| proxy environment variables set to a dead proxy | direct origin connection, 1 connection |

`nghttpx` reproducer:

| Case | Backend H2 stream limit | Mode | Backend connections |
| --- | ---: | --- | ---: |
| `nghttpx-default-limit1-concurrent` | 1 | reverse proxy | 2 simultaneous |
| `nghttpx-proxy-limit1-concurrent` | 1 | `--http2-proxy` | 2 simultaneous |
| `nghttpx-default-limit100-concurrent` | 100 | reverse proxy | 1 |
| `nghttpx-proxy-limit100-concurrent` | 100 | `--http2-proxy` | 1 |
| `nghttpx-default-limit1-serial` | 1 | reverse proxy | 1 |
| `nghttpx-proxy-limit1-serial` | 1 | `--http2-proxy` | 1 |

`verify.py` re-decoded the saved HTTP/2 captures and passed. The failing runs show connection 1 handling `/warmup` and `/one`, then connection 2 being accepted for `/two` before connection 1 closed.

## Inconsistency Reason

RFC 9113 §9.1 tells clients to avoid more than one HTTP/2 connection to the same derived host and port unless an exception applies. In the reproduced `nghttpx` cases, both backend connections use the same host, port, protocol, and configuration. The only trigger is remote stream concurrency exhaustion.

This leaves the `nghttpx` default HTTP/2 backend connection-management behavior as the supported standards-compliance concern.
