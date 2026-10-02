# OpenSSL accepts unrequested ticket_request in TLS 1.3 response extensions

## Summary

OpenSSL does not implement `ticket_request(58)` as a built-in TLS extension. That absence is not the issue by itself. The issue is that OpenSSL's TLS 1.3 client treats extension type `58` as an unknown extension and silently accepts it in server response messages where RFC 9846 requires rejection unless the extension was first requested.

The clearest runtime reproducer injects extension type `58` into the server `Certificate` message. `Certificate` is not a valid TLS 1.3 placement for `ticket_request`, and it is one of the server response messages covered by RFC 9846's `unsupported_extension` rule. OpenSSL completes the TLS 1.3 handshake instead of aborting.

## Standard Requirement

Official standard references:

- [RFC 9846 Section 4.3, "Extensions"](https://www.rfc-editor.org/rfc/rfc9846.html#section-4.3)
- [RFC 9846 Section 6.2, "Error Alerts"](https://www.rfc-editor.org/rfc/rfc9846.html#section-6.2)
- [RFC 9149 Section 3, "Ticket Requests"](https://www.rfc-editor.org/rfc/rfc9149.html#section-3)
- RFC 9149 Errata ID 8996: https://www.rfc-editor.org/errata/eid8996

RFC 9846 defines the extension type:

```text
ticket_request(58),                         /* RFC 9149 */
```

RFC 9846 Table 1 records the TLS 1.3 placement:

```text
| ticket_request [RFC9149]                         |      CH, EE |
```

RFC 9846 also defines the response-extension rule:

```text
Implementations MUST NOT send extension responses (i.e., in the
ServerHello, EncryptedExtensions, HelloRetryRequest, and Certificate
messages) if the remote endpoint did not send the corresponding
extension requests, with the exception of the "cookie" extension in
the HelloRetryRequest.  Upon receiving such an extension, an endpoint
MUST abort the handshake with an "unsupported_extension" alert.
```

The alert definition matches that rule:

```text
unsupported_extension:  Sent by endpoints receiving any handshake
message containing an extension in a ServerHello,
HelloRetryRequest, EncryptedExtensions, or Certificate not first
offered in the corresponding ClientHello or CertificateRequest.
```

RFC 9846 Section 9.3 separately requires unknown-extension tolerance for a server receiving `ClientHello`, and for a client receiving `CertificateRequest` or `NewSessionTicket`. That exception does not cover `EncryptedExtensions` or `Certificate`.

RFC 9149 has a reported technical erratum against Section 3. The erratum changes the expected alert from `illegal_parameter` to `unsupported_extension`, which is consistent with RFC 9846's response-extension rule. It does not change the `CH, EE` registry placement.

## Relevant Source Code

OpenSSL has no built-in `ticket_request` extension definition.

`include/openssl/tls1.h:148`

```c
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

Unknown extensions are accepted by the generic collection path.

`ssl/statem/extensions.c:747`

```c
/* Unknown extension. We allow it */
*found = NULL;
return 1;
```

`ssl/statem/extensions.c:966`

```c
/* The server must tolerate the unknown extension and complete. */
if (thisex == NULL)
    continue;
```

OpenSSL does have a helper for contexts where unknown TLS 1.3 response extensions are not ignored.

`ssl/statem/extensions.c:801`

```c
/*
 * Verify that all extensions in |packet| are known built-in or custom
 * extension types. This is used for TLS 1.3 server extension responses where
 * unknown extensions are not ignored.
 */
int tls_validate_no_unknown_extensions(SSL_CONNECTION *s, PACKET *packet,
    unsigned int context)
```

`ssl/statem/extensions.c:849`

```c
SSLfatal(s, SSL_AD_UNSUPPORTED_EXTENSION,
    SSL_R_UNSOLICITED_EXTENSION);
return 0;
```

The TLS 1.3 `ServerHello` and `HelloRetryRequest` client paths call this helper.

`ssl/statem/statem_clnt.c:1800`

```c
if (!tls_collect_extensions(s, &extpkt, SSL_EXT_TLS1_3_HELLO_RETRY_REQUEST,
        &extensions, NULL, 1)
    || !tls_validate_no_unknown_extensions(s, &extpkt,
        SSL_EXT_TLS1_3_HELLO_RETRY_REQUEST)
```

`ssl/statem/statem_clnt.c:1964`

```c
if (SSL_CONNECTION_IS_TLS13(s)
    && !tls_validate_no_unknown_extensions(s, &extpkt, context))
```

The TLS 1.3 `Certificate` and `EncryptedExtensions` client paths do not call the helper before parsing all extensions.

`ssl/statem/statem_clnt.c:2423`

```c
if (!tls_collect_extensions(s, &extensions,
        SSL_EXT_TLS1_3_CERTIFICATE, &rawexts,
        NULL, chainidx == 0)
    || !tls_parse_all_extensions(s, SSL_EXT_TLS1_3_CERTIFICATE,
        rawexts, x, chainidx,
        PACKET_remaining(pkt) == 0)) {
```

`ssl/statem/statem_clnt.c:4489`

```c
if (!tls_collect_extensions(s, &extensions,
        SSL_EXT_TLS1_3_ENCRYPTED_EXTENSIONS, &rawexts,
        NULL, 1)
    || !tls_parse_all_extensions(s, SSL_EXT_TLS1_3_ENCRYPTED_EXTENSIONS,
        rawexts, NULL, 0, 1)) {
```

## Implementation Behavior

Because type `58` is not in OpenSSL's built-in extension table, `tls_collect_extensions()` treats it as unknown. In the `Certificate` and `EncryptedExtensions` paths, unknown extensions are skipped before the built-in unsolicited-extension check can run.

This is appropriate for contexts where TLS 1.3 explicitly requires unknown-extension tolerance, such as `CertificateRequest` and `NewSessionTicket`. It is not appropriate for server response messages that RFC 9846 names in the `unsupported_extension` rule.

## Inconsistency Reason

RFC 9846 records `ticket_request` as `CH, EE`. It also requires endpoints to abort with `unsupported_extension` when they receive an extension response in `ServerHello`, `HelloRetryRequest`, `EncryptedExtensions`, or `Certificate` that was not first requested.

OpenSSL instead accepts extension type `58` in TLS 1.3 `Certificate`, even though `Certificate` is not a valid placement for `ticket_request` and the client did not offer the extension. OpenSSL also accepts type `58` in `EncryptedExtensions` without a corresponding client request. The implementation's unknown-extension tolerance is therefore applied too broadly.

## Runtime Evidence

On 2026-08-10, I reran focused TLSProxy probes against the local OpenSSL build. The probes injected extension type `58` as an unrequested TLS 1.3 server response extension and recorded the resulting handshake behavior.

Build and runtime environment:

```text
target repo: implementions/openssl-master
OpenSSL: OpenSSL 4.1.0-dev
platform: linux-x86_64
Perl: v5.38.2
environment: TOP=.; PERL5LIB=util/perl:test
```

### Misplaced ticket_request in Certificate

Action: I ran the certificate-extension probe with extension type `58`, causing the server `Certificate` message to carry an unrequested `ticket_request` extension.

Observed output:

```text
MODIFY Certificate add extension 58
SSL handshake has read 2441 bytes and written 1557 bytes
Protocol: TLSv1.3
EXT_TYPE 58
INJECTED 1
SUCCESS 1
TLS13 1
ALERT_SIDE server
ALERT_DESC 0
```

Observed stderr summary:

```text
CONNECTION ESTABLISHED
Protocol version: TLSv1.3
SSL3 alert read:warning:close notify
SSL3 alert write:warning:close notify
```

This demonstrates that the proxy injected extension type `58` into the TLS 1.3 `Certificate` message and OpenSSL completed the handshake. The expected behavior is a client abort with `unsupported_extension`.

### Unrequested ticket_request in EncryptedExtensions

Action: I ran the EncryptedExtensions probe with extension type `58`, causing the server `EncryptedExtensions` message to carry an unrequested `ticket_request` extension.

Observed output:

```text
MODIFY EncryptedExtensions add extension 58
SSL handshake has read 2441 bytes and written 1557 bytes
Protocol: TLSv1.3
EXT_TYPE 58
INJECTED 1
SUCCESS 1
TLS13 1
ALERT_SIDE server
ALERT_DESC 0
```

Observed stderr summary:

```text
CONNECTION ESTABLISHED
Protocol version: TLSv1.3
SSL3 alert read:warning:close notify
SSL3 alert write:warning:close notify
```

`EncryptedExtensions` is a valid placement for `ticket_request` only when the extension is a response to a client request. This probe injected type `58` without a corresponding `ClientHello` request, and OpenSSL still completed the handshake.

## Impact

This is a TLS 1.3 extension-validation compliance gap. A peer can send unsupported or unrequested response extensions in messages where RFC 9846 expects `unsupported_extension` failure, and OpenSSL may continue the handshake. The immediate practical impact appears low, but the behavior can mask invalid extension negotiation and weakens strict protocol validation.

## Fix Direction

Ensure TLS 1.3 client processing rejects unknown or unrequested response extensions in all RFC 9846 response contexts:

- `ServerHello`
- `HelloRetryRequest`
- `EncryptedExtensions`
- `Certificate`

The existing `tls_validate_no_unknown_extensions()` helper already implements the intended rejection behavior for some client response paths. The TLS 1.3 `EncryptedExtensions` and `Certificate` paths should be updated so unknown response extensions such as `ticket_request(58)` cannot pass through `tls_collect_extensions()` silently.
