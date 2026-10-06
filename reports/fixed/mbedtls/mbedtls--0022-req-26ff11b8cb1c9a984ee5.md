# TLS 1.3 `close_notify` Does Not Close the Local Write Side

## Summary

Scope clarification:

- This is a sender-side issue in the library API path.
- It is not about whether the caller later closes the underlying TCP socket.
- The issue is that the same TLS context can still send `ApplicationData` after a successful `mbedtls_ssl_close_notify()`.

## Standard Requirement

- Standard: [RFC 8446 Section 6.1](https://www.rfc-editor.org/rfc/rfc8446.html#section-6.1)
- Compatibility note: [RFC 9846 Section 6.1](https://www.rfc-editor.org/rfc/rfc9846.html#section-6.1) preserves the same write-side closure semantics

Normative text:

```text
close_notify:  This alert notifies the recipient that the sender will
   not send any more messages on this connection.  Any data received
   after a closure alert has been received MUST be ignored.
```

```text
Either party MAY initiate a close of its write side of the connection
by sending a "close_notify" alert.
```

```text
Each party MUST send a "close_notify" alert before closing its write
side of the connection, unless it has already sent some error alert.
This does not have any effect on its read side of the connection.
```

Interpretation:

- `close_notify` is the sender's declaration that it will send no more TLS messages on that connection.
- Sending `close_notify` initiates closure of the sender's write side.
- The read side may remain open, but the local write side must not remain usable for more `ApplicationData`.
- The standards text defines the protocol semantics of the connection state. It does not require a specific local C API error code, but it does require that the sender stop generating further TLS messages on that connection.

## Relevant Source Code

### `library/ssl_msg.c:5862-5915`

```c
static int ssl_write_real(mbedtls_ssl_context *ssl,
                          const unsigned char *buf, size_t len)
{
    int ret = mbedtls_ssl_get_max_out_record_payload(ssl);
    const size_t max_len = (size_t) ret;
    ...
    ssl->out_msglen  = len;
    ssl->out_msgtype = MBEDTLS_SSL_MSG_APPLICATION_DATA;
    if (len > 0) {
        memcpy(ssl->out_msg, buf, len);
    }

    if ((ret = mbedtls_ssl_write_record(ssl, SSL_FORCE_FLUSH)) != 0) {
        MBEDTLS_SSL_DEBUG_RET(1, "mbedtls_ssl_write_record", ret);
        return ret;
    }
    ...
    return (int) len;
}
```

This internal write path turns the caller's buffer into `ApplicationData` and
forces a record flush through `mbedtls_ssl_write_record(ssl, SSL_FORCE_FLUSH)`.
A successful return is therefore evidence of post-close TLS data generation, not
just an abstract API-side state change.

### `library/ssl_msg.c:5921-5949`

```c
int mbedtls_ssl_write(mbedtls_ssl_context *ssl, const unsigned char *buf, size_t len)
{
    int ret = MBEDTLS_ERR_ERROR_CORRUPTION_DETECTED;
    ...
    if (ssl->state != MBEDTLS_SSL_HANDSHAKE_OVER) {
        if ((ret = mbedtls_ssl_handshake(ssl)) != 0) {
            return ret;
        }
    }

    ret = ssl_write_real(ssl, buf, len);
    ...
    return ret;
}
```

`mbedtls_ssl_write()` does not check any local write-closed or
close-notify-sent state before entering `ssl_write_real()`.

### `library/ssl_msg.c:6060-6081`

```c
int mbedtls_ssl_close_notify(mbedtls_ssl_context *ssl)
{
    int ret = MBEDTLS_ERR_ERROR_CORRUPTION_DETECTED;
    ...
    if (mbedtls_ssl_is_handshake_over(ssl) == 1) {
        if ((ret = mbedtls_ssl_send_alert_message(ssl,
                                                  MBEDTLS_SSL_ALERT_LEVEL_WARNING,
                                                  MBEDTLS_SSL_ALERT_MSG_CLOSE_NOTIFY)) != 0) {
            return ret;
        }
    }
    ...
    return 0;
}
```

`mbedtls_ssl_close_notify()` sends the alert but does not record a state change
that prevents later application writes on the same context.

## Implementation Behavior

Putting the code paths together:

1. `mbedtls_ssl_close_notify()` sends `close_notify` and returns `0` on success.
2. It does not mark the local write side as closed.
3. A later `mbedtls_ssl_write()` on the same `mbedtls_ssl_context` still enters
   `ssl_write_real()`.
4. `ssl_write_real()` labels the output as `MBEDTLS_SSL_MSG_APPLICATION_DATA`
   and forces a record write.
5. Therefore the library can emit `ApplicationData` records after
   `close_notify`.

## Inconsistency Reason

RFC 8446 and RFC 9846 both use `close_notify` for orderly closure of one
direction of the connection and define it as the sender's statement that it
will not send any more messages on that connection.

The implementation treats `close_notify` only as an alert-emission helper. It
does not close the local write API path, so the same TLS context remains able
to generate more `ApplicationData` afterward.

The sender-side violation is already complete at that point. The protocol does
not require a second proof that the peer application consumed the data, nor does
it require a particular local API failure mode. Once the library accepts and
emits post-close `ApplicationData` on the same connection, it has already
violated the `close_notify` write-side semantics.

## Runtime Evidence

### Recheck On 2026-08-04

Execution:

```powershell
runtime_tests/bin/psk_close_notify_post_write_probe.exe |
    Tee-Object -FilePath runtime_tests/logs/0022_close_notify_post_write_rerun_2026-08-04.txt
```

Probe logic:

```c
const unsigned char payload[] = "after-close";
...
close_ret = mbedtls_ssl_close_notify(&ssl);
post_write_ret = mbedtls_ssl_write(&ssl, payload, sizeof(payload) - 1);
```

Observed output:

```text
server_bind_ret=0
server_accept_ret=0
server_handshake_ret=0
close_notify_ret=0
post_close_write_ret=11
```

Interpretation:

- The probe completes a TLS 1.3 PSK handshake.
- `mbedtls_ssl_close_notify()` succeeds.
- The same `mbedtls_ssl_context` then accepts another application write.
- `post_close_write_ret=11` matches the 11-byte payload `after-close`.
- Because the write path uses `mbedtls_ssl_write_record(ssl, SSL_FORCE_FLUSH)`,
  this is runtime evidence of post-close `ApplicationData` generation, not just
  an unconsumed application buffer.

## Impact

- Applications can continue sending TLS application data after they believe they
  have performed an orderly TLS close.
- Peer behavior may become ambiguous: a compliant peer must ignore the data,
  while the sender-side API still reports a successful write.
- This can hide shutdown bugs and create incorrect assumptions about connection
  lifecycle.

## Fix Direction

- Record an outbound close state when `mbedtls_ssl_close_notify()` succeeds.
- Reject subsequent `mbedtls_ssl_write()` calls on the same context with an
  explicit error once the local write side has been closed.
- Add a regression test that performs `close_notify` and then verifies that a
  second `mbedtls_ssl_write()` cannot send `ApplicationData`.

## Decision Reason

The standard text, the implementation path, and the rerun on August 4, 2026 all
point to the same conclusion:

- `close_notify` closes the sender's write side.
- mbedTLS does not record or enforce that state.
- The probe successfully writes 11 bytes of `ApplicationData` after
  `close_notify`.
- RFC 9846 preserves the same core rule, so this conclusion does not depend on
  superseded wording.

This report should be treated as `issue_found`.
