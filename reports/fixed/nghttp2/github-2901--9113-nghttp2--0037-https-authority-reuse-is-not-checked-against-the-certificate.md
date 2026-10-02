# HTTPS authority reuse is not checked against the certificate

## Standard

RFC 9113 Section 9.1.1 allows HTTP/2 connection reuse for different URI authorities only while the server is authoritative. For `https` resources, lines 3170-3174 require:

```text
For "https" resources, connection reuse additionally depends on
having a certificate that is valid for the host in the URI.  The
certificate presented by the server MUST satisfy any checks that the
client would perform when forming a new TLS connection for the host
in the URI.
```

So a reused TLS connection carrying `https` requests for `one.example` and `two.example` needs a certificate valid for those URI hosts.

## Code

`nghttp2-master/src/shrpx.cc:1677-1680` defaults to preserving the incoming host:

```c++
auto &httpconf = config->http;
httpconf.server_name = "nghttpx"sv;
httpconf.no_host_rewrite = true;
```

`nghttp2-master/src/shrpx_http2_session.cc:431-445` creates the backend TLS connection using backend host/SNI:

```c++
auto sni_name = addr_->sni.empty() ? addr_->host : addr_->sni;
if (!util::numeric_host(sni_name.data())) {
  SSL_set_tlsext_host_name(conn_.tls.ssl, sni_name.data());
}
auto maybe_tls_session =
  tls::reuse_tls_session(addr_->tls_session_cache);
```

`nghttp2-master/src/shrpx_http2_session.cc:2116-2121` verifies the certificate once after that handshake:

```c++
if (!get_config()->tls.insecure) {
  if (auto rv = tls::check_cert(conn_.tls.ssl, addr_, raddr_); !rv) {
    downstream_failure(addr_, raddr_);
    return rv;
  }
}
```

`nghttp2-master/src/shrpx_tls.cc:1984-1987` shows the verified name is still backend host/SNI, not each URI authority:

```c++
auto hostname = addr->sni.empty() ? addr->host : addr->sni;
return check_cert(ssl, raddr, hostname);
```

`nghttp2-master/src/shrpx_http2_downstream_connection.cc:268-280` then preserves request authority when host rewrite is disabled:

```c++
auto no_host_rewrite = httpconf.no_host_rewrite || config->http2_proxy ||
                       req.regular_connect_method();
auto authority = http2session_->get_addr()->hostport;
if (no_host_rewrite && !req.authority.empty()) {
  authority = req.authority;
}
```

With `upgrade-scheme`, `nghttp2-master/src/shrpx_http2_downstream_connection.cc:316-323` forwards the request as `https`:

```c++
if (addr->tls && addr->upgrade_scheme && req.scheme == "http"sv) {
  nva.push_back(http2::make_field(":scheme"sv, "https"sv));
} else {
  nva.push_back(http2::make_field(":scheme"sv, req.scheme));
}
```

The reusable HTTP/2 backend session comes from `addr->http2_extra_freelist` (`nghttp2-master/src/shrpx_client_handler.cc:737-786`), so reuse is keyed by backend address, not by each forwarded URI host.

## Runtime Evidence

A focused reuse probe generated a trusted backend certificate valid only for `IP:127.0.0.1`, then ran nghttpx 1.70.90:

```text
nghttpx --conf=empty-nghttpx.conf --frontend=127.0.0.1,<port>;no-tls --backend=127.0.0.1,<port>;;proto=h2;tls;upgrade-scheme --cacert=ca.crt --workers=1 --log-level=INFO --backend-http2-connections-per-worker=1
```

The run observed:

```json
{
  "certificate_san": "IP:127.0.0.1 only",
  "backend_connection_count": 1,
  "backend_schemes": ["https", "https"],
  "backend_authorities": ["one.example", "two.example"],
  "response_statuses": ["200", "200"],
  "backend_errors": []
}
```

Control run with `--host-rewrite` produced backend authorities `127.0.0.1:<port>, 127.0.0.1:<port>`, confirming the probe distinguishes rewritten authority from default preserved authority.

## Decision

This behavior was reproduced. nghttpx validates the backend certificate only for the backend host/SNI, but can reuse one HTTP/2 TLS backend connection for multiple forwarded `https` URI authorities not covered by that certificate. RFC 9113 requires the certificate to be valid for each reused HTTPS URI host.
