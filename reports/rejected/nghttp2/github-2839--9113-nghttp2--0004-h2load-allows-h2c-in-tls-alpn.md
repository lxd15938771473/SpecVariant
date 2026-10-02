# h2load Allows h2c in TLS ALPN

## Problem Description

The configurable `h2load --alpn-list` path can send `h2c` as a TLS ALPN token, even though RFC 9113 forbids clients from sending `h2c` over TLS.

## Summary

RFC 9113 forbids a TLS client from sending the `h2c` protocol identifier. nghttp2's default client paths use `h2`, but the `h2load` tool accepts `--alpn-list=h2c`, serializes it as an ALPN token, and passes it to OpenSSL for a TLS ClientHello. The issue is configuration-dependent and limited to `h2load`; it is not the default behavior of `nghttp` or `libnghttp2`.

## Standard Requirement

Source: RFC 9113, Section 3.2, "Starting HTTP/2 for `https` URIs"

```text
HTTP/2 over TLS uses the "h2" protocol identifier.  The "h2c"
protocol identifier MUST NOT be sent by a client or selected by a
server; the "h2c" protocol identifier describes a protocol that does
not use TLS.
```

Interpretation: for HTTP/2 over TLS, the client must use `h2` in ALPN and must not offer `h2c`.

## Relevant Source Code

`nghttp` hardcodes the correct TLS ALPN token:

```c++
// src/nghttp.cc:2174-2176
SSL_CTX_set_alpn_protos(
  ssl_ctx, reinterpret_cast<const uint8_t *>(NGHTTP2_H2_ALPN.data()),
  NGHTTP2_H2_ALPN.size());
```

`h2load` defaults are also correct:

```c++
// src/h2load.cc:2638
constexpr auto DEFAULT_ALPN_LIST = "h2,http/1.1"sv;
```

The problem is the configurable `--alpn-list` path:

```c++
// src/h2load.cc:3288-3292
case 19:
  // alpn-list option
  config.alpn_list =
    util::parse_config_str_list(std::string_view{optarg});
  break;
```

The parser only splits the string; it does not reject `h2c`:

```c++
// src/util.cc:1030-1033
std::vector<std::string> parse_config_str_list(std::string_view s, char delim) {
  return s | std::ranges::views::split(delim) |
         std::ranges::to<std::vector<std::string>>();
}
```

`h2load` then converts each configured token to ALPN wire format and gives the bytes to OpenSSL:

```c++
// src/h2load.cc:3333-3340
if (config.alpn_list.empty()) {
  config.alpn_list = util::parse_config_str_list(DEFAULT_ALPN_LIST);
}

// serialize the APLN tokens
for (auto &proto : config.alpn_list) {
  proto.insert(std::ranges::begin(proto), static_cast<char>(proto.size()));
}
```

```c++
// src/h2load.cc:3612-3618
std::vector<unsigned char> proto_list;
for (const auto &proto : config.alpn_list) {
  std::ranges::copy(proto, std::back_inserter(proto_list));
}

SSL_CTX_set_alpn_protos(ssl_ctx, proto_list.data(),
                        static_cast<uint32_t>(proto_list.size()));
```

## Implementation Behavior

For `--alpn-list=h2c`, `h2load` derives the ALPN byte sequence `03 68 32 63` (`\x03h2c`) and installs it on the TLS client context. There is no check that removes or rejects `h2c` when the target URI uses `https`.

If a server later selected an unsupported token, `h2load` would fail protocol setup, but the RFC violation here is earlier: the client must not send `h2c` in TLS ALPN at all.

## Inconsistency Reason

RFC 9113 says a TLS client MUST NOT send `h2c`. `h2load` lets the user configure `h2c` and sends the resulting ALPN token through `SSL_CTX_set_alpn_protos`. This contradicts the client-side prohibition for HTTP/2 over TLS.

## Runtime Evidence

A focused packet-level probe constructed the same ALPN list that `h2load --alpn-list` passes to OpenSSL and captured the resulting ClientHello. The positive control used `h2`; the reproducer used `h2c`.

Observed result:

```json
{
  "positive_control": {
    "alpn_argument": "h2",
    "captured_client_hello_alpn": ["h2"]
  },
  "reproducer": {
    "alpn_argument": "h2c",
    "captured_client_hello_alpn": ["h2c"],
    "h2load_serialized_hex": "03683263"
  }
}
```

The current build contains only the `nghttp2` library target and no `h2load` executable. A direct `h2load` invocation was therefore not available; this packet-level run verifies the same TLS ALPN wire behavior used by the `h2load` code path above.

## Impact

Low severity. Defaults are compliant, and the issue requires explicit `h2load --alpn-list=h2c` configuration. It can still generate a TLS ClientHello that violates RFC 9113 and may confuse conformance tests or protocol diagnostics.

## Fix Direction

When `h2load` is used with an `https` URI, reject `h2c` in `--alpn-list` before serializing the ALPN list, or silently remove it with a clear warning.
