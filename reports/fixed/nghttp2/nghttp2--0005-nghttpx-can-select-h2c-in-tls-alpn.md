# nghttpx can select `h2c` in TLS ALPN

## Problem Description

The configurable `nghttpx` TLS frontend can select `h2c` from `--alpn-list` when the client offers it, despite RFC 9113 forbidding server selection of `h2c` over TLS.
- Not affected: `nghttpd`; default `nghttpx` config (`h2,http/1.1`)

## Standard Requirement

RFC 9113 Section 3.1 defines `h2` as the identifier for HTTP/2 over TLS and says prior `h2c` Upgrade usage is deprecated. Section 3.2 gives the operative rule:

```text
HTTP/2 over TLS uses the "h2" protocol identifier.  The "h2c"
protocol identifier MUST NOT be sent by a client or selected by a
server; the "h2c" protocol identifier describes a protocol that does
not use TLS.
```

So a TLS ALPN server selector must not return `h2c`, even if the peer offers it.

## Relevant Source Code

`nghttpd` is safe because it only selects `h2`:

```cpp
// src/HttpServer.cc:2076-2079
if (!util::select_h2(out, outlen, in, inlen)) {
  return SSL_TLSEXT_ERR_NOACK;
}
return SSL_TLSEXT_ERR_OK;
```

```cpp
// src/util.cc:969-972
bool select_h2(const unsigned char **out, unsigned char *outlen,
               const unsigned char *in, unsigned int inlen) {
  return select_proto(out, outlen, in, inlen, NGHTTP2_H2_ALPN);
}
```

`nghttpx` has a safe default, but accepts an arbitrary configured ALPN list:

```cpp
// src/shrpx.cc:1617,3633-3635
constexpr auto DEFAULT_ALPN_LIST = "h2,http/1.1"sv;
if (tlsconf.alpn_list.empty()) {
  tlsconf.alpn_list = util::split_str(DEFAULT_ALPN_LIST, ',');
}
```

```cpp
// src/shrpx_config.cc:4388-4391
case SHRPX_OPTID_ALPN_LIST:
  config->tls.alpn_list = util::split_str(config->balloc, optarg, ',');

  return {};
```

The TLS callback selects the first overlap from that configured list, with no `h2c` filter:

```cpp
// src/shrpx_tls.cc:557-574
for (const auto &alpn : get_config()->tls.alpn_list) {
  for (auto p = in, end = in + inlen; p < end;) {
    auto proto_id = p + 1;
    auto proto_len = *p;

    if (alpn.size() == proto_len &&
        memcmp(alpn.data(), proto_id, alpn.size()) == 0) {
      *out = proto_id;
      *outlen = proto_len;
      return SSL_TLSEXT_ERR_OK;
    }

    p += 1 + proto_len;
  }
}

return SSL_TLSEXT_ERR_NOACK;
```

`src/shrpx_client_handler.cc:612-670` later rejects protocols other than `h2` and `http/1.1`, but that happens after TLS ALPN has already selected `h2c`.

## Runtime Evidence

The current build cannot run a full `nghttpx` process:

```text
cmake --build <current build directory> --target nghttpx
mingw32-make: *** No rule to make target 'nghttpx'.  Stop.
```

Reason: this run was configured with `ENABLE_APP=OFF`, so it contains no `nghttpx` executable. I ran a focused probe that reproduces the cited ALPN selector and performs a real local TLS ALPN handshake.

Observed output:

```text
nghttpd fixed h2, client h2c only: None
nghttpd fixed h2, client h2c+h2: 'h2'
nghttpx default h2,http/1.1, client h2c+h2: 'h2'
nghttpx configured h2c,h2, client h2c+h2: 'h2c'
nghttpx configured h2c only, client h2c only: 'h2c'
python ssl TLS ALPN server h2c,h2 client h2c,h2: client='h2c' server='h2c'
```

## Inconsistency Reason

This behavior was reproduced in the configurable `nghttpx` TLS ALPN path: if `h2c` is placed in `--alpn-list` and offered by the client, the server can select `h2c` over TLS, which RFC 9113 forbids. A minimal fix would reject `h2c` in TLS `--alpn-list` parsing or skip it in the TLS ALPN callback.
