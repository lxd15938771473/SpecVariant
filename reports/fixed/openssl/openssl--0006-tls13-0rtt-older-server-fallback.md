# OpenSSL TLS 1.3 0-RTT Older-Server Fallback


## Summary

RFC 8446 Appendix D.3 requires a client that attempts 0-RTT to fail the current connection if the server replies with TLS 1.2 or older. OpenSSL's client accepts the TLS 1.2 `ServerHello` into the legacy handshake path instead of failing immediately because the client version-selection path does not check the early-data state.

The issue was rechecked with a focused runtime reproducer. The reproducer first obtains a resumable TLS 1.3 session with `max_early_data`, then sends one byte of 0-RTT data and injects a syntactically valid TLS 1.2 `ServerHello`. OpenSSL transitions the client connection to `TLSv1.2` and waits for further handshake input (`SSL_ERROR_WANT_READ`) with an empty error queue.

## Standard Requirement

- Official standard: [RFC 8446, Appendix D.3, "0-RTT Backward Compatibility"](https://www.rfc-editor.org/rfc/rfc8446.html#appendix-D.3)

The relevant normative requirement is that a client which attempts to send 0-RTT data **"MUST fail a connection"** when the received `ServerHello` selects TLS 1.2 or older. The same paragraph permits a later retry with 0-RTT disabled and says the client should keep TLS 1.3 enabled to avoid downgrade risk.

D.1 allows ordinary TLS 1.3 clients to interoperate with older servers, but D.3 narrows that behavior for 0-RTT: once 0-RTT has been attempted, an older `ServerHello` is not just a normal fallback case.

## Relevant Source Code

### `ssl/statem/statem_clnt.c:260`

```c
case TLS_ST_EARLY_DATA:
    /*
     * We've not actually selected TLSv1.3 yet, but we have sent early
     * data. The only thing allowed now is a ServerHello or a
     * HelloRetryRequest.
     */
    if (mt == SSL3_MT_SERVER_HELLO) {
        st->hand_state = TLS_ST_CR_SRVR_HELLO;
        return 1;
    }
```

When the client is in `TLS_ST_EARLY_DATA`, the state machine still permits `ServerHello`. This is not itself wrong for TLS 1.3, but it means the subsequent `ServerHello` processing path must enforce the D.3 older-version failure rule.

### `ssl/statem/statem_clnt.c:1917`

```c
if (!hrr) {
    if (!ssl_choose_client_version(s, sversion, extensions)) {
        /* SSLfatal() already called */
        goto err;
    }
}
```

For a non-HRR `ServerHello`, OpenSSL delegates version selection to `ssl_choose_client_version()`. There is no local check here for `s->early_data_state` plus `sversion <= TLS1_2_VERSION`.

### `ssl/statem/statem_lib.c:2329`

```c
int ssl_choose_client_version(SSL_CONNECTION *s, int version,
    RAW_EXTENSION *extensions)
{
    ...
    s->version = version;

    /* This will overwrite s->version if the extension is present */
    if (!tls_parse_extension(s, TLSEXT_IDX_supported_versions,
            SSL_EXT_TLS1_2_SERVER_HELLO
                | SSL_EXT_TLS1_3_SERVER_HELLO,
            extensions,
            NULL, 0)) {
        s->version = origv;
        return 0;
    }
```

If the TLS 1.2 `ServerHello` has no TLS 1.3 `supported_versions` selection, `s->version` remains the legacy `ServerHello` version. This function performs version bounds and downgrade-sentinel checks, then installs the version-specific method; it does not reject the older version because 0-RTT was attempted.

### `ssl/statem/extensions_clnt.c:1150`

```c
if (s->early_data_state != SSL_EARLY_DATA_CONNECTING
    || (s->session->ext.max_early_data == 0
        && (psksess == NULL || psksess->ext.max_early_data == 0))) {
    s->max_early_data = 0;
    return EXT_RETURN_NOT_SENT;
}
...
s->ext.early_data = SSL_EARLY_DATA_REJECTED;
s->ext.early_data_ok = 1;
```

OpenSSL has a clear early-data attempt path: `early_data` is sent only when the client is in `SSL_EARLY_DATA_CONNECTING` and the session/PSK allows early data. The runtime reproducer exercises this path and confirms one byte of 0-RTT was accepted by `SSL_write_early_data()`.

## Implementation Behavior

Expected behavior:

- Client sends or attempts to send 0-RTT.
- Server replies with `ServerHello` selecting TLS 1.2 or older.
- Client fails the current connection immediately.

Observed behavior:

- Client successfully enters the 0-RTT write path.
- Client reads a TLS 1.2 `ServerHello`.
- Client sets the connection version to `TLSv1.2`.
- Client returns `SSL_ERROR_WANT_READ` with no queued OpenSSL error, meaning it is waiting for the next TLS 1.2 handshake message rather than failing the connection.

## Runtime Evidence

### Reproducer

The focused probe first completed a TLS 1.3 handshake to obtain a resumable session with `max_early_data=16384`. It then began a new connection with that session, wrote one byte of 0-RTT data, and injected a minimal valid TLS 1.2 `ServerHello`. The injected message included empty `renegotiation_info` signaling so that legacy-renegotiation policy would not mask the version-fallback behavior.

Observed output:

```text
initial_version=TLSv1.3 session_protocol=0x0304 session_resumable=1 session_max_early_data=16384
fake_first_early_ret=1 err=0 version=TLSv1.3 init=0 early_status=1 written=1
fake_clienthello_bytes=1858 sidlen=32 selected_cipher=0xc02f
msg_cb dir=read record_version=0x0304 type=handshake hs_type=2 len=49
fake_after_tls12_sh_connect_ret=-1 err=2 peek_error=0x0 version=TLSv1.2 init=0 want_r=1 want_w=0 early_status=1 written=1
fake_followup_SSL_connect_ret=-1 err=2 peek_error=0x0 version=TLSv1.2 state="SSLv3/TLS read server hello" init=0 want_r=1 want_w=0 early_status=1
fake_probe_decision=continued_tls12_after_0rtt_serverhello
```

Interpretation:

- `session_max_early_data=16384` confirms the session is eligible for 0-RTT.
- `fake_first_early_ret=1` and `written=1` confirm OpenSSL accepted a one-byte 0-RTT write attempt.
- `hs_type=2` confirms the injected message is a `ServerHello`.
- `version=TLSv1.2`, `err=2`, `want_r=1`, and `peek_error=0x0` confirm the client continued into the TLS 1.2 path and waited for more input rather than failing.

### Interference Checks

Two false-positive traps were checked while constructing the reproducer:

- A stock OpenSSL TLS 1.2 server may fail earlier because it rejects the TLS 1.3 `early_data` extension. That does not test the client-side D.3 behavior, so the final reproducer injects a minimal older `ServerHello`.
- A TLS 1.2 `ServerHello` without secure renegotiation signaling triggers OpenSSL's `unsafe legacy renegotiation disabled` failure. The final reproducer includes an empty `renegotiation_info` extension so that the observed behavior is not caused by that unrelated policy.

## Inconsistency Reason

The standard requires the client to fail the current connection once these two facts are true:

- 0-RTT was attempted.
- The received `ServerHello` selects TLS 1.2 or older.

The OpenSSL path permits `ServerHello` during early-data state and then lets the ordinary client version-selection logic accept TLS 1.2. Because no 0-RTT-specific older-version failure check is applied, the client can continue waiting for TLS 1.2 handshake messages instead of terminating the connection attempt.

## Impact

This violates a downgrade-protection rule intended for mixed TLS 1.3/TLS 1.2 deployments. A client that attempted 0-RTT should not silently proceed on the same connection after an older `ServerHello`; it should fail that connection and only retry with 0-RTT disabled while continuing to offer TLS 1.3.

## Fix Direction

Add an explicit client-side check after parsing the `ServerHello` version and before accepting a non-TLS-1.3 version:

- If the client attempted 0-RTT, and
- the selected version is TLS 1.2 or older, and
- this is not a valid TLS 1.3 HelloRetryRequest,

then call `SSLfatal()` and abort the current connection attempt. The retry policy should be left to the caller or higher-level connection logic, but any retry should disable only 0-RTT, not TLS 1.3.
