# TLS 1.3 Certificate accepts unrequested record_size_limit extension

## Summary

OpenSSL does not implement `record_size_limit` as a built-in TLS extension. That alone would not be a protocol bug. The confirmed issue is narrower: a TLS 1.3 client accepts extension type `28` in the server `Certificate` message and completes the handshake. RFC 9846 assigns `record_size_limit` to `CH, EE` only, and a server response extension in `Certificate` must correspond to a prior client request; otherwise the client must abort with `unsupported_extension`.

## Standard Requirement

Official standards: [RFC 9846 Section 4.3, "Extensions"](https://www.rfc-editor.org/rfc/rfc9846.html#section-4.3); [RFC 9846 Section 4.5.1, "Certificate"](https://www.rfc-editor.org/rfc/rfc9846.html#section-4.5.1); [RFC 8449 Section 4, "The record_size_limit Extension"](https://www.rfc-editor.org/rfc/rfc8449.html#section-4); [RFC 8449 Section 7, "IANA Considerations"](https://www.rfc-editor.org/rfc/rfc8449.html#section-7).

Short standard excerpts:

```text
record_size_limit(28)
record_size_limit [RFC8449] | CH, EE
unsupported_extension
CertificateRequest or NewSessionTicket
```

Standard interpretation:

- RFC 9846 includes `record_size_limit(28)` in the TLS extension registry and Table 1 allows it only in `ClientHello` and `EncryptedExtensions`.
- RFC 8449 confirms that in TLS 1.3 the server sends `record_size_limit` in `EncryptedExtensions`, and its IANA registration assigns code point `28` with TLS 1.3 messages `CH` or `EE`.
- RFC 9846 treats `ServerHello`, `EncryptedExtensions`, `HelloRetryRequest`, and `Certificate` as server extension response locations. If such a response extension was not first requested, the endpoint must abort with `unsupported_extension`.
- RFC 9846 Section 4.5.1 separately says extensions in a server `Certificate` message must correspond to extensions in the `ClientHello`.
- The TLS 1.3 rule that clients ignore unrecognized extensions is explicitly scoped to `CertificateRequest` and `NewSessionTicket`, not to server `Certificate`.

Therefore, a TLS 1.3 client must not silently accept `record_size_limit(28)` in a server `Certificate` message. Because `record_size_limit` is permitted only in `CH, EE`, the expected behavior for the tested server `Certificate` response is a fatal `unsupported_extension` alert.

## Relevant Source Code

`include/openssl/tls1.h:147-163`

```c
/* ExtensionType value from RFC8879 */
#define TLSEXT_TYPE_compress_certificate 27

/* ExtensionType value from RFC4507 */
#define TLSEXT_TYPE_session_ticket 35

/* As defined for TLS1.3 */
#define TLSEXT_TYPE_psk 41
#define TLSEXT_TYPE_early_data 42
#define TLSEXT_TYPE_supported_versions 43
#define TLSEXT_TYPE_cookie 44
#define TLSEXT_TYPE_psk_kex_modes 45
#define TLSEXT_TYPE_certificate_authorities 47
#define TLSEXT_TYPE_post_handshake_auth 49
#define TLSEXT_TYPE_signature_algorithms_cert 50
#define TLSEXT_TYPE_key_share 51
#define TLSEXT_TYPE_quic_transport_parameters 57
```

There is no built-in `TLSEXT_TYPE_record_size_limit` constant or stock parser/constructor for extension type `28` in the inspected OpenSSL tree.

`ssl/statem/statem_clnt.c:2415-2428`

```c
if (SSL_CONNECTION_IS_TLS13(s)) {
    RAW_EXTENSION *rawexts = NULL;
    PACKET extensions;

    if (!PACKET_get_length_prefixed_2(pkt, &extensions)) {
        SSLfatal(s, SSL_AD_DECODE_ERROR, SSL_R_BAD_LENGTH);
        goto err;
    }
    if (!tls_collect_extensions(s, &extensions,
            SSL_EXT_TLS1_3_CERTIFICATE, &rawexts,
            NULL, chainidx == 0)
        || !tls_parse_all_extensions(s, SSL_EXT_TLS1_3_CERTIFICATE,
            rawexts, x, chainidx,
            PACKET_remaining(pkt) == 0)) {
```

The TLS 1.3 client `Certificate` path collects and parses certificate-entry extensions, but it does not call `tls_validate_no_unknown_extensions()`.

`ssl/statem/extensions.c:696-749`

```c
/*
 * Verify whether we are allowed to use the extension |type| in the current
 * |context|. Returns 1 to indicate the extension is allowed or unknown or 0 to
 * indicate the extension is not allowed. If returning 1 then |*found| is set to
 * the definition for the extension we found.
 */
static int verify_extension(SSL_CONNECTION *s, unsigned int context,
    unsigned int type, custom_ext_methods *meths,
    RAW_EXTENSION *rawexlist, RAW_EXTENSION **found)
{
    ...
    /* Unknown extension. We allow it */
    *found = NULL;
    return 1;
}
```

For an unrecognized type such as `28`, `verify_extension()` returns success with `*found == NULL`.

`ssl/statem/extensions.c:966-968`

```c
/* The server must tolerate the unknown extension and complete. */
if (thisex == NULL)
    continue;
```

The generic collection path skips unknown extensions. Because the `Certificate` path does not run a no-unknown response-extension check, the injected extension is ignored.

`ssl/statem/statem_clnt.c:1964-1966`

```c
if (SSL_CONNECTION_IS_TLS13(s)
    && !tls_validate_no_unknown_extensions(s, &extpkt, context))
    /* SSLfatal() already called */
```

The TLS 1.3 `ServerHello` path demonstrates that OpenSSL has a helper for TLS 1.3 server response locations where unknown response extensions must not be ignored. That helper is not applied to the TLS 1.3 server `Certificate` path above.

## Implementation Behavior

When a TLS 1.3 server `Certificate` message contains extension type `28` with zero-length `extension_data`, OpenSSL treats it as an unknown certificate-entry extension. The extension is accepted by `verify_extension()`, skipped by `tls_collect_extensions()`, and never rejected by the client `Certificate` processing path.

This behavior is not merely lack of support for an optional extension. The peer sends an extension response in a prohibited TLS 1.3 message, and OpenSSL completes the handshake instead of enforcing the response-extension rule.

## Inconsistency Reason

RFC 9846 permits `record_size_limit` only in `ClientHello` and `EncryptedExtensions`. It also requires an endpoint to abort when it receives an unrequested response extension in `ServerHello`, `EncryptedExtensions`, `HelloRetryRequest`, or `Certificate`.

OpenSSL accepts type `28` in a TLS 1.3 server `Certificate` message. That message is not an allowed `record_size_limit` location, and `Certificate` is not one of the TLS 1.3 messages where clients must ignore unrecognized extensions. The implementation is therefore inconsistent with the required `unsupported_extension` behavior.

## Runtime Evidence

On 2026-08-10, I reran the TLSProxy certificate-extension probe against OpenSSL `4.1.0-dev` on `linux-x86_64`. The probe injected extension type `28` with zero-length `extension_data` into the TLS 1.3 server `Certificate` message.

Key rerun output:

```text
MODIFY Certificate add extension 28
CONNECTION ESTABLISHED
Protocol version: TLSv1.3
1 server accepts that finished
EXT_TYPE 28
INJECTED 1
SUCCESS 1
TLS13 1
ALERT_SIDE server
ALERT_DESC 0
EXIT_CODE 0
```

Expected behavior: the client aborts the TLS 1.3 handshake with `unsupported_extension`.

Actual behavior: the injected extension is present (`INJECTED 1`), the connection remains TLS 1.3 (`TLS13 1`), and the handshake succeeds (`SUCCESS 1`, `CONNECTION ESTABLISHED`). The observed alert is a warning `close_notify` at normal shutdown, not a fatal `unsupported_extension`.

## Impact

The impact is a TLS 1.3 conformance failure in client-side extension validation. A peer can send an unrequested extension response in `Certificate`, including a type that RFC 9846 assigns to `CH, EE` only, and OpenSSL silently accepts it instead of aborting the handshake.

## Fix Direction

Apply TLS 1.3 response-extension validation to server `Certificate` extension vectors on the client path. The fix should reject unknown or unrequested response extensions in `Certificate` with `SSL_AD_UNSUPPORTED_EXTENSION`, while preserving the RFC-mandated ignore behavior for `CertificateRequest` and `NewSessionTicket`.

Regression coverage should include a TLSProxy test that injects extension type `28` into TLS 1.3 server `Certificate` and expects the client to abort with `unsupported_extension`.
