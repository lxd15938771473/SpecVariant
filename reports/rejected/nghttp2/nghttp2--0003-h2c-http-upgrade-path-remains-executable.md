# h2c HTTP Upgrade path remains executable

## Problem Description

The executable HTTP/1.1 Upgrade path still permits cleartext HTTP/2 startup through `Upgrade: h2c` instead of the RFC 9113 prior-knowledge path.

## Summary

RFC 9113 requires cleartext HTTP/2 for `http` URIs to be established with prior knowledge. nghttp2 still provides an executable HTTP/1.1 Upgrade path that sends `Upgrade: h2c` and `HTTP2-Settings`, then enters HTTP/2 after a `101 Switching Protocols` response.

## Standard Requirement

Official standard: [RFC 9113](https://www.rfc-editor.org/rfc/rfc9113.html)

- Section 3, `Starting HTTP/2`: `HTTP/2 support for "http" URIs can only be discovered by out-of-band means and requires prior knowledge`
- Section 3.1, `HTTP/2 Version Identification`: describes `h2c` as the former HTTP Upgrade token and deprecates that usage together with `HTTP2-Settings`.
- Section 11.1 and 11.2 mark `HTTP2-Settings` and the `h2c` upgrade token as obsolete/removed.

Expected behavior for `http` URI startup:

```text
TCP connect
PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n
SETTINGS ...
```

Not the HTTP/1.1 Upgrade flow:

```text
GET / HTTP/1.1
Connection: Upgrade, HTTP2-Settings
Upgrade: h2c
HTTP2-Settings: ...
```

## Relevant Source Code

`src/nghttp.cc:539`

```c++
bool HttpClient::need_upgrade() const {
  return config.upgrade && scheme == "http";
}
```

`src/nghttp.cc:884`

```c++
auto headers = Headers{{"host", hostport},
                       {"connection", "Upgrade, HTTP2-Settings"},
                       {"upgrade", NGHTTP2_CLEARTEXT_PROTO_VERSION_ID},
                       {"http2-settings", std::move(token68)},
```

`src/nghttp.cc:973`

```c++
if (upgrade_response_status_code != 101) {
  std::println(stderr, "[ERROR] HTTP Upgrade failed");

  return std::unexpected{Error::INTERNAL};
}
```

`src/nghttp.cc:1043`

```c++
rv = nghttp2_session_upgrade2(session, settings_payload.data(),
                              settings_payloadlen, head_request,
                              stream_user_data);
```

`lib/includes/nghttp2/nghttp2.h:4309`

```c
NGHTTP2_EXTERN int nghttp2_session_upgrade2(
  nghttp2_session *session, const uint8_t *settings_payload,
  size_t settings_payloadlen, int head_request, void *stream_user_data);
```

## Implementation Behavior

When `--upgrade` is selected for an `http` URI, `nghttp` sends an HTTP/1.1 Upgrade request containing `Upgrade: h2c` and `HTTP2-Settings`. If the peer returns status `101`, nghttp2 calls `nghttp2_session_upgrade2()` and continues as an HTTP/2 session.

The library API also accepts the same Upgrade transition directly.

## Inconsistency Reason

RFC 9113 removes the HTTP/1.1 `h2c` Upgrade discovery path for cleartext HTTP/2. The implementation still exposes and executes that path. Therefore, an `http` URI can be switched to HTTP/2 through in-band HTTP/1.1 Upgrade instead of prior-knowledge startup.

## Runtime Evidence

A ctypes smoke test loaded the built libnghttp2 library, built a SETTINGS payload with `nghttp2_pack_settings_payload2()`, then called `nghttp2_session_upgrade2()` for client and server sessions.

Result:

```json
{
  "callbacks_new_rv": 0,
  "settings_payload_len": 12,
  "settings_payload_hex": "[hex omitted]",
  "observations": [
    {
      "side": "client",
      "session_new_rv": 0,
      "upgrade2_rv": 0,
      "stream1_exists": true,
      "stream1_state": "NGHTTP2_STREAM_STATE_HALF_CLOSED_LOCAL"
    },
    {
      "side": "server",
      "session_new_rv": 0,
      "upgrade2_rv": 0,
      "stream1_exists": true,
      "stream1_state": "NGHTTP2_STREAM_STATE_HALF_CLOSED_REMOTE"
    }
  ]
}
```

`upgrade2_rv: 0` and `stream1_exists: true` show that the Upgrade transition is executable in the current build.

Note: the available build has `ENABLE_LIB_ONLY=ON`, so this runtime check targets the exported library path rather than the `nghttp` command-line binary.

## Impact

This is a strict RFC 9113 conformance issue for cleartext HTTP/2 startup. A peer can still use the removed `h2c` HTTP Upgrade mechanism through nghttp2 code paths.

## Fix Direction

Disable the RFC 9113 `h2c` HTTP Upgrade path for RFC 9113 operation: remove or gate `--upgrade`, stop emitting `Upgrade: h2c` and `HTTP2-Settings`, and prevent `nghttp2_session_upgrade2()` from being used as an RFC 9113 startup path.
