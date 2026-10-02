# h2load can use HTTP/2 over TLS 1.1

## Summary

## Standard Requirement

Official standard: https://www.rfc-editor.org/rfc/rfc9113.html#section-9.2

RFC 9113 Section 9.2, “Use of TLS Features”:

```text
Implementations of HTTP/2 MUST use TLS version 1.2 [TLS12] or higher
for HTTP/2 over TLS.
```

Meaning: if ALPN selects HTTP/2 over TLS, the negotiated TLS protocol must not be TLS 1.0 or TLS 1.1.

## Relevant Source Code

`implementions/nghttp2-master/nghttp2-master/src/tls.h:78-83` sets the shared TLS minimum to TLS 1.0:

```c++
inline constexpr auto NGHTTP2_TLS_MIN_VERSION = TLS1_VERSION;
#ifdef TLS1_3_VERSION
inline constexpr auto NGHTTP2_TLS_MAX_VERSION = TLS1_3_VERSION;
#else
inline constexpr auto NGHTTP2_TLS_MAX_VERSION = TLS1_2_VERSION;
#endif
```

`implementions/nghttp2-master/nghttp2-master/src/tls.cc:117-125` has the correct helper, but it must be called by each HTTP/2 TLS path:

```c++
bool check_http2_tls_version(SSL *ssl) {
  auto tls_ver = SSL_version(ssl);
  return tls_ver >= TLS1_2_VERSION;
}

bool check_http2_requirement(SSL *ssl) {
  return check_http2_tls_version(ssl) && !check_http2_cipher_block_list(ssl);
}
```

`implementions/nghttp2-master/nghttp2-master/src/h2load.cc:1207-1227` creates an HTTP/2 session from ALPN `h2`; no TLS-version check appears on this path:

```c++
std::expected<void, Error> Client::connection_made() {
  if (ssl) {
    report_tls_info();
    SSL_get0_alpn_selected(ssl, &next_proto, &next_proto_len);
    ...
      } else if (util::check_h2_is_selected(proto)) {
        session = std::make_unique<Http2Session>(this);
```

`implementions/nghttp2-master/nghttp2-master/src/h2load.cc:2705-2709`, `3159-3162`, and `3582-3590` allow a caller-provided TLS 1.2-or-earlier cipher list and apply it with the low minimum version:

```c++
--ciphers=<SUITE>
```

```c++
case 2:
  config.ciphers = optarg;
  break;
```

```c++
ssl_ctx_set_proto_versions(ssl_ctx, NGHTTP2_TLS_MIN_VERSION,
                           NGHTTP2_TLS_MAX_VERSION)
SSL_CTX_set_cipher_list(ssl_ctx, config.ciphers.c_str())
```

Counter-checks: `implementions/nghttp2-master/nghttp2-master/src/HttpServer.cc:831-832` calls `check_http2_requirement()` and `implementions/nghttp2-master/nghttp2-master/src/shrpx.cc:1617-1619` defaults nghttpx to `TLSv1.2`.

## Implementation Behavior

For `h2load`, the effective sequence is: allow TLS down to TLS 1.0, apply user-provided ciphers, negotiate ALPN, then start HTTP/2 if ALPN is `h2`. Because the existing `check_http2_requirement()` helper is not called here, TLS 1.1 is accepted when the cipher list permits it.

## Inconsistency Reason

The standard requires TLS 1.2 or higher for every HTTP/2-over-TLS connection. The confirmed `h2load` path can negotiate TLS 1.1, select ALPN `h2`, and send the HTTP/2 connection preface. That is inconsistent with RFC 9113 Section 9.2.

## Runtime Evidence

The probe starts a local TLS server fixed to TLS 1.1 with ALPN `h2`, then runs `h2load` with one request, one connection, `--connection-active-timeout=3`, `--ciphers=ALL:@SECLEVEL=0`, and `--alpn-list=h2`.

```sh
h2load -n1 -c1 -v --connection-active-timeout=3 \
  --ciphers=ALL:@SECLEVEL=0 --alpn-list=h2 https://127.0.0.1:<port>/
```

Observed output from the latest run:

```text
[h2load-legacy-cipher]
tls_version: TLSv1.1
alpn: h2
received_ascii: PRI * HTTP/2.0

TLS Protocol: TLSv1.1
Cipher: ECDHE-RSA-AES256-SHA
Application protocol: h2
requests: 1 total, 1 started
```

The later request failure is expected because the probe server only proves TLS/ALPN/session start, not a full HTTP/2 response. The violation is already visible before that failure.

## Impact

A caller can run `h2load` as HTTP/2 over TLS 1.1 by supplying a legacy cipher list. This weakens the RFC 9113 TLS floor for the benchmarking client path.

## Fix Direction

In `h2load` after TLS handshake and ALPN selection, reject `h2` unless `check_http2_requirement(ssl)` succeeds, or set the TLS minimum for HTTP/2 TLS client contexts to `TLS1_2_VERSION` so TLS 1.0/1.1 cannot be negotiated.
