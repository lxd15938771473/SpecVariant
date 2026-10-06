# Empty server Certificate uses alert 41 instead of decode_error

## Summary

This report has been reorganized and rechecked against both RFC 8446 and the current standard text in RFC 9846. A fresh focused runtime rerun on August 4, 2026 reached the same conclusion as the static review: when the TLS 1.3 client-side Certificate-processing path handles an empty server Certificate message, mbedTLS selects alert `41` instead of the required `decode_error(50)`.

This report deduplicates multiple candidate-level records that resolved to the same root cause.

## Standard Requirement

- Primary section: RFC 8446 Section 4.4.2.4 Receiving a Certificate Message ([RFC 8446](https://www.rfc-editor.org/rfc/rfc8446.html))
- Latest recheck: RFC 9846 Section 4.5.1.3 Receiving a Certificate Message ([RFC 9846](https://www.rfc-editor.org/rfc/rfc9846.html))
- Alert semantics recheck: RFC 9846 Section 6.2 ([RFC 9846](https://www.rfc-editor.org/rfc/rfc9846.html))
- Alert registry recheck: RFC 9846 Appendix B.2 ([RFC 9846](https://www.rfc-editor.org/rfc/rfc9846.html), [RFC 9846](https://www.rfc-editor.org/rfc/rfc9846.html))

RFC 8446 text:

```text
If the server supplies an empty Certificate message, the client MUST
abort the handshake with a "decode_error" alert.
```

RFC 9846 keeps the same requirement:

```text
If the server supplies an empty Certificate message, the client MUST
abort the handshake with a "decode_error" alert.
```

Interpretation:

The condition is that a client receives an empty server `Certificate` message. The implementation must abort the handshake, and RFC 9846 Section 6.2 further states that when the specification says "abort the handshake with an X alert", the implementation must send alert `X` if it sends any alert. RFC 9846 still lists `41` as `no_certificate_RESERVED` and `50` as `decode_error`, so sending alert `41` is not an acceptable substitute.

## Relevant Source Code

The TLS 1.3 client state machine reaches `mbedtls_ssl_tls13_process_certificate()` when processing the server Certificate message. An empty certificate chain is not treated as `decode_error` in parsing. Instead, the later validation path maps it to the legacy `no_certificate` alert, and the pending-alert send path then emits that exact value.

### `library/ssl_tls13_client.c:2575-2585`

```c
static int ssl_tls13_process_server_certificate(mbedtls_ssl_context *ssl)
{
    int ret;

    ret = mbedtls_ssl_tls13_process_certificate(ssl);
    if (ret != 0) {
        return ret;
    }

    mbedtls_ssl_handshake_set_state(ssl, MBEDTLS_SSL_CERTIFICATE_VERIFY);
    return 0;
}
```

This shows that the focused rerun calls the same implementation entry point that the normal TLS 1.3 client handshake uses for the server Certificate message.

### `library/ssl_tls13_generic.c:489-507`

```c
    if ((certificate_request_context_len != 0) ||
        (certificate_list_len >= 0x10000)) {
        MBEDTLS_SSL_DEBUG_MSG(1, ("bad certificate message"));
        MBEDTLS_SSL_PEND_FATAL_ALERT(MBEDTLS_SSL_ALERT_MSG_DECODE_ERROR,
                                     MBEDTLS_ERR_SSL_DECODE_ERROR);
        return MBEDTLS_ERR_SSL_DECODE_ERROR;
    }

    /* In case we tried to reuse a session but it failed */
    if (ssl->session_negotiate->peer_cert != NULL) {
        mbedtls_x509_crt_free(ssl->session_negotiate->peer_cert);
        mbedtls_free(ssl->session_negotiate->peer_cert);
    }

    /* This is used by ssl_tls13_validate_certificate() */
    if (certificate_list_len == 0) {
        ssl->session_negotiate->peer_cert = NULL;
        ret = 0;
        goto exit;
    }
```

Malformed fields and empty certificate lists are deliberately handled differently here: malformed messages raise `decode_error`, but `certificate_list_len == 0` does not.

### `library/ssl_tls13_generic.c:675-705`

```c
    if (ssl->session_negotiate->peer_cert == NULL) {
        MBEDTLS_SSL_DEBUG_MSG(1, ("peer has no certificate"));

#if defined(MBEDTLS_SSL_SRV_C)
        if (ssl->conf->endpoint == MBEDTLS_SSL_IS_SERVER) {
            /* The client was asked for a certificate but didn't send
             * one. The client should know what's going on, so we
             * don't send an alert.
             */
            ssl->session_negotiate->verify_result = MBEDTLS_X509_BADCERT_MISSING;
            if (authmode == MBEDTLS_SSL_VERIFY_OPTIONAL) {
                return 0;
            } else {
                MBEDTLS_SSL_PEND_FATAL_ALERT(
                    MBEDTLS_SSL_ALERT_MSG_NO_CERT,
                    MBEDTLS_ERR_SSL_NO_CLIENT_CERTIFICATE);
                return MBEDTLS_ERR_SSL_NO_CLIENT_CERTIFICATE;
            }
        }
#endif /* MBEDTLS_SSL_SRV_C */

#if defined(MBEDTLS_SSL_CLI_C)
        if (ssl->conf->endpoint == MBEDTLS_SSL_IS_CLIENT) {
            MBEDTLS_SSL_PEND_FATAL_ALERT(MBEDTLS_SSL_ALERT_MSG_NO_CERT,
                                         MBEDTLS_ERR_SSL_FATAL_ALERT_MESSAGE);
            return MBEDTLS_ERR_SSL_FATAL_ALERT_MESSAGE;
        }
```

The client branch explicitly selects `MBEDTLS_SSL_ALERT_MSG_NO_CERT`, that is, alert value `41`, when `peer_cert == NULL`.

### `include/mbedtls/ssl.h:539-550`

```c
#define MBEDTLS_SSL_ALERT_MSG_HANDSHAKE_FAILURE     40  /* 0x28 */
#define MBEDTLS_SSL_ALERT_MSG_NO_CERT               41  /* 0x29 */
#define MBEDTLS_SSL_ALERT_MSG_BAD_CERT              42  /* 0x2A */
#define MBEDTLS_SSL_ALERT_MSG_UNSUPPORTED_CERT      43  /* 0x2B */
#define MBEDTLS_SSL_ALERT_MSG_CERT_REVOKED          44  /* 0x2C */
#define MBEDTLS_SSL_ALERT_MSG_CERT_EXPIRED          45  /* 0x2D */
#define MBEDTLS_SSL_ALERT_MSG_CERT_UNKNOWN          46  /* 0x2E */
#define MBEDTLS_SSL_ALERT_MSG_ILLEGAL_PARAMETER     47  /* 0x2F */
#define MBEDTLS_SSL_ALERT_MSG_UNKNOWN_CA            48  /* 0x30 */
#define MBEDTLS_SSL_ALERT_MSG_ACCESS_DENIED         49  /* 0x31 */
#define MBEDTLS_SSL_ALERT_MSG_DECODE_ERROR          50  /* 0x32 */
#define MBEDTLS_SSL_ALERT_MSG_DECRYPT_ERROR         51  /* 0x33 */
```

The code-level constants clearly distinguish `41` from `50`, so this is not a naming-only artifact.

### `library/ssl_msg.c:6236-6272`

```c
int mbedtls_ssl_handle_pending_alert(mbedtls_ssl_context *ssl)
{
    int ret;

    /* No pending alert, return success*/
    if (ssl->send_alert == 0) {
        return 0;
    }

    ret = mbedtls_ssl_send_alert_message(ssl,
                                         MBEDTLS_SSL_ALERT_LEVEL_FATAL,
                                         ssl->alert_type);
    ...
}

void mbedtls_ssl_pend_fatal_alert(mbedtls_ssl_context *ssl,
                                  unsigned char alert_type,
                                  int alert_reason)
{
    ssl->send_alert = 1;
    ssl->alert_type = alert_type;
    ssl->alert_reason = alert_reason;
}
```

The chosen alert value is stored in `ssl->alert_type` and sent unchanged. There is no TLS 1.3 remapping step that could turn `41` into `50` later.

## Implementation Behavior

The actual behavior is:

1. The TLS 1.3 client processes the server Certificate through `mbedtls_ssl_tls13_process_certificate()`.
2. If the message is structurally malformed, for example nonzero `certificate_request_context_len` or oversized `certificate_list_len`, the code raises `decode_error`.
3. If `certificate_list_len == 0`, parsing does not fail. Instead, `peer_cert` is set to `NULL`.
4. The later client-side validation branch sees `peer_cert == NULL` and pends `MBEDTLS_SSL_ALERT_MSG_NO_CERT`.
5. The alert send path then emits that exact value on the wire.

## Inconsistency Reason

The standard requires this behavior: if the server sends an empty `Certificate` message, the client must abort the handshake with `decode_error`.

The implementation does this instead: if the server sends an empty `Certificate` message, the client still aborts the handshake, but it selects the legacy `no_certificate_RESERVED(41)` alert value rather than `decode_error(50)`.

So the mismatch is not about whether the handshake aborts. The mismatch is the specific alert value used during the abort, and RFC 9846 makes that value mandatory.

## Runtime Evidence

### Round 1

- Status: `passed`
- Date: `2026-08-04`
- Type: `focused runtime probe`

This rerun executed a small probe against the real compiled implementation path rather than relying only on static reading.


The probe feeds the following empty TLS 1.3 `Certificate` handshake message into `mbedtls_ssl_tls13_process_certificate()`:

```text
0b 00 00 04 00 00 00 00
```

Where:

- `0b` is the `Certificate` handshake type.
- `00 00 04` is the body length.
- `00` is the `certificate_request_context` length.
- `00 00 00` is the `certificate_list` length, that is, an empty certificate chain.

Observed output:

```text
ret=-30592
ret_str=SSL - A fatal alert message was received from our peer
send_alert=1
alert_type=41
alert_reason=-30592
decode_error=50
no_cert_legacy_constant=41
```

Key observations:

- `send_alert=1` shows that the implementation did pend a fatal alert.
- `alert_type=41` shows that the selected alert is the legacy `no_certificate` value.
- `decode_error=50` shows that the current build still defines `decode_error` as a different value.
- `no_cert_legacy_constant=41` matches `alert_type=41`, which aligns the runtime result with the static code path.

This rerun therefore upgrades the conclusion from "the code appears to send 41" to "the implementation actually selects 41 at runtime".

## Impact

This issue makes the TLS 1.3 handling of an empty server `Certificate` non-compliant with the standard. The handshake still aborts, but the alert description is wrong. That can affect:

- interoperability with peers or test suites that validate the required alert value;
- debugging and protocol analysis, because the observed alert is a reserved legacy value rather than `decode_error`;
- compliance audits, where this becomes a stable alert-mapping defect.

## Fix Direction

The fix should keep the existing "abort on empty server Certificate" behavior, but change the client-side TLS 1.3 path to pend `MBEDTLS_SSL_ALERT_MSG_DECODE_ERROR` instead of `MBEDTLS_SSL_ALERT_MSG_NO_CERT`.

More specifically:

1. Keep the current requirement that an empty server certificate chain aborts the handshake.
2. In the client `peer_cert == NULL` branch in `library/ssl_tls13_generic.c`, change the alert type to `MBEDTLS_SSL_ALERT_MSG_DECODE_ERROR`.
3. Add a focused regression test for the TLS 1.3 client path with an empty server `Certificate` and assert that the selected alert description is `50`.
