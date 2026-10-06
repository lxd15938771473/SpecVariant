# Client sends a configured but unsuitable certificate instead of an empty Certificate

## Problem Description

RFC 8446 requires a TLS 1.3 client to send an empty `Certificate` message when client authentication was requested but no suitable client certificate is available. The current mbedTLS client does follow that rule when no client certificate is configured at all, but it does not follow it when a certificate is configured yet cannot satisfy the server's `CertificateRequest` constraints. In that case it still sends the configured certificate and only fails later during `CertificateVerify`.

This report deduplicates multiple candidate-level records that resolved to the same root cause.

## Standard Requirement

- Relevant sections:
  - `4.3.2 CertificateRequest` ([RFC 8446](https://www.rfc-editor.org/rfc/rfc8446.html))
  - `4.4.2 Certificate` ([RFC 8446](https://www.rfc-editor.org/rfc/rfc8446.html))
  - `4.4.2.3 Client Certificate Selection` ([RFC 8446](https://www.rfc-editor.org/rfc/rfc8446.html))
  - `4.4.3 Certificate Verify` ([RFC 8446](https://www.rfc-editor.org/rfc/rfc8446.html))

> If the server requests client authentication but no suitable certificate is available, the client MUST send a Certificate message containing no certificates.

Interpretation:

Applies when the server has sent `CertificateRequest` and the client's configured certificate set does not satisfy the request. Suitability is not limited to the presence of a local certificate object; it also depends on the request constraints, including acceptable signature algorithms. When no suitable certificate exists, the client must send an empty `Certificate` and continue with `Finished`. It must not send a non-matching certificate and then fail only at `CertificateVerify`.

## Relevant Source Code

The TLS 1.3 client parses the server's `signature_algorithms` request, but it does not use that information to decide whether to send an empty `Certificate`. Instead, it sends a non-empty `Certificate` whenever `mbedtls_ssl_own_cert(ssl)` is non-null and defers the compatibility check to the `CertificateVerify` path.

### `library/ssl_tls13_client.c:2478-2520`

```c
        switch (extension_type) {
            case MBEDTLS_TLS_EXT_SIG_ALG:
                MBEDTLS_SSL_DEBUG_MSG(3,
                                      ("found signature algorithms extension"));
                ret = mbedtls_ssl_parse_sig_alg_ext(ssl, p,
                                                    p + extension_data_len);
                if (ret != 0) {
                    return ret;
                }

                break;

            default:
                MBEDTLS_SSL_PRINT_EXT(
                    3, MBEDTLS_SSL_HS_CERTIFICATE_REQUEST,
                    extension_type, "( ignored )");
                break;
        }

        p += extension_data_len;
    }

    /* RFC 8446 section 4.3.2
     *
     * The "signature_algorithms" extension MUST be specified
     */
    if ((handshake->received_extensions & MBEDTLS_SSL_EXT_MASK(SIG_ALG)) == 0) {
        MBEDTLS_SSL_DEBUG_MSG(3,
                              ("no signature algorithms extension found"));
        goto decode_error;
    }

    ssl->handshake->client_auth = 1;
```

The client records that authentication was requested and parses `signature_algorithms`, but it does not derive a "no suitable certificate" decision here.

### `library/ssl_tls13_client.c:2649-2678`

```c
static int ssl_tls13_write_client_certificate(mbedtls_ssl_context *ssl)
{
    int non_empty_certificate_msg = 0;

    MBEDTLS_SSL_DEBUG_MSG(1,
                          ("Switch to handshake traffic keys for outbound traffic"));
    mbedtls_ssl_set_outbound_transform(ssl, ssl->handshake->transform_handshake);

#if defined(MBEDTLS_SSL_TLS1_3_KEY_EXCHANGE_MODE_EPHEMERAL_ENABLED)
    if (ssl->handshake->client_auth) {
        int ret = mbedtls_ssl_tls13_write_certificate(ssl);
        if (ret != 0) {
            return ret;
        }

        if (mbedtls_ssl_own_cert(ssl) != NULL) {
            non_empty_certificate_msg = 1;
        }
    } else {
        MBEDTLS_SSL_DEBUG_MSG(2, ("skip write certificate"));
    }
#endif

    if (non_empty_certificate_msg) {
        mbedtls_ssl_handshake_set_state(ssl,
                                        MBEDTLS_SSL_CLIENT_CERTIFICATE_VERIFY);
    } else {
        MBEDTLS_SSL_DEBUG_MSG(2, ("skip write certificate verify"));
        mbedtls_ssl_handshake_set_state(ssl, MBEDTLS_SSL_CLIENT_FINISHED);
    }
```

Whether the outgoing `Certificate` is treated as empty depends only on `mbedtls_ssl_own_cert(ssl) != NULL`, not on whether the configured certificate is suitable for the current `CertificateRequest`.

### `library/ssl_tls13_generic.c:970-1033`

```c
    for (; *sig_alg != MBEDTLS_TLS1_3_SIG_NONE; sig_alg++) {
        if (!mbedtls_ssl_sig_alg_is_offered(ssl, *sig_alg)) {
            continue;
        }

        if (!mbedtls_ssl_tls13_sig_alg_for_cert_verify_is_supported(*sig_alg)) {
            continue;
        }

        if (!mbedtls_ssl_tls13_check_sig_alg_cert_key_match(*sig_alg, own_key)) {
            continue;
        }

        if ((ret = mbedtls_pk_sign_ext((mbedtls_pk_sigalg_t) pk_type, own_key,
                                       md_alg, verify_hash, verify_hash_len,
                                       p + 4, (size_t) (end - (p + 4)), &signature_len)) != 0) {
            continue;
        }

        break;
    }

    if (*sig_alg == MBEDTLS_TLS1_3_SIG_NONE) {
        MBEDTLS_SSL_DEBUG_MSG(1, ("no suitable signature algorithm"));
        MBEDTLS_SSL_PEND_FATAL_ALERT(MBEDTLS_SSL_ALERT_MSG_HANDSHAKE_FAILURE,
                                     MBEDTLS_ERR_SSL_HANDSHAKE_FAILURE);
        return MBEDTLS_ERR_SSL_HANDSHAKE_FAILURE;
    }
```

The actual compatibility check is delayed until `CertificateVerify`. By this point a non-empty client `Certificate` may already have been sent on the wire.

### `include/mbedtls/ssl.h:3541-3546`

```c
 * \note           On client, only the first call has any effect. That is,
 *                 only one client certificate can be provisioned. The
 *                 server's preferences in its CertificateRequest message will
 *                 be ignored and our only cert will be sent regardless of
 *                 whether it matches those preferences - the server can then
 *                 decide what it wants to do with it.
```

The public API documentation explicitly describes the same behavior: the client ignores `CertificateRequest` preferences and sends its single configured certificate regardless of suitability.

## Runtime Evidence

### Focused rerun on 2026-08-04

Both runtime scenarios used the TLS 1.3 example client and server. The server
required client authentication and constrained acceptable client-signature
algorithms to `rsa_pkcs1_sha512`, `rsa_pss_rsae_sha512`,
`rsa_pss_rsae_sha384`, and `ecdsa_secp521r1_sha512`.

#### Scenario A: no client certificate configured

Client setup:

- `crt_file=none`
- `key_file=none`

Observed behavior:

- The client received `CertificateRequest` and reached `MBEDTLS_SSL_CLIENT_CERTIFICATE`.
- It entered `=> write certificate`.
- The client emitted the minimal empty TLS 1.3 `Certificate` payload:

```text
0b 00 00 04 00 00 00 00
```

- The client then logged `skip write certificate verify` and transitioned to `MBEDTLS_SSL_CLIENT_FINISHED`.
- The server parsed the empty `Certificate` and terminated because authentication was required, logging `No client certification received from the client, but required by the authentication mode`.

This confirms that the current build does have a correct empty-certificate branch when no client certificate is configured.

#### Scenario B: configured but unsuitable client certificate

The configured certificate is ECDSA P-256 / SHA-256, while the server's `CertificateRequest` does not allow `ecdsa_secp256r1_sha256`.

Observed behavior:

- The client received `CertificateRequest: signature_algorithms(13) extension received`.
- It still reached `MBEDTLS_SSL_CLIENT_CERTIFICATE`.
- It entered `=> write certificate` and logged `own certificate #1`.
- The client emitted a non-empty `Certificate` payload beginning with:

```text
0b 00 02 2d 00 00 02 29 00 02 24 30 82 02 20 30
```

- The server subsequently parsed that client certificate and logged `peer certificate #1`.
- Only after sending the non-empty `Certificate` did the client enter `=> write certificate verify` and fail with `no suitable signature algorithm`.
- The server then received a fatal alert 40 (`handshake_failure`).

This shows that the implementation does not treat "configured but unsuitable certificate" as the RFC's "no suitable certificate available" case. Instead, it sends the unsuitable certificate first and fails later during `CertificateVerify`.

## Inconsistency Reason

- RFC 8446 defines the empty-certificate path in terms of certificate suitability, not merely the presence or absence of a configured certificate object.
- The current mbedTLS client uses a narrower condition: if `mbedtls_ssl_own_cert(ssl)` is non-null, it sends a non-empty `Certificate`.
- As a result, a configured but unsuitable client certificate is transmitted on the wire, even though RFC 8446 requires an empty `Certificate` followed by `Finished`.

## Decision Reason

- The standard text in [RFC 8446](https://www.rfc-editor.org/rfc/rfc8446.html) requires an empty `Certificate` whenever no suitable client certificate is available.
- Static inspection shows that mbedTLS decides between empty and non-empty `Certificate` solely from whether a local certificate is configured, and postpones suitability checking to `CertificateVerify`.
- The focused reruns confirm both branches in the current build: the empty-certificate branch is taken when no certificate is configured, but the unsuitable-certificate branch still sends a non-empty certificate before failing.

The final verdict is therefore `issue_found`.
