# OpenSSL TLS 1.3 client accepts unrequested unknown response extension type 39

## Summary

This is a real issue, but the scope is narrow. OpenSSL 4.1.0-dev does not implement `supported_ekt_ciphers` as a stock TLS extension, and RFC 9846 does not require all TLS implementations to implement that optional extension. The absence of an EKT implementation is therefore not a defect by itself.

The defect is the response-validation behavior: when a TLS 1.3 server sends extension type `39` (`supported_ekt_ciphers`) as an unrequested response in `Certificate` or `EncryptedExtensions`, the OpenSSL client completes the handshake instead of aborting with `unsupported_extension`.

## Standard Requirement

Official standard: [RFC 9846, Section 4.3, Extensions](https://www.rfc-editor.org/rfc/rfc9846.html#section-4.3)

The response-extension rule says:

```text
Upon receiving such an extension, an endpoint MUST abort the handshake with an "unsupported_extension" alert.
```

RFC 9846 lists `supported_ekt_ciphers` as a TLS 1.3 extension that may appear only in `ClientHello` and `EncryptedExtensions`:

```text
| supported_ekt_ciphers [RFC8870]                  |      CH, EE |
```

The alert definition in [RFC 9846 Section 6.2](https://www.rfc-editor.org/rfc/rfc9846.html#section-6.2) also ties `unsupported_extension` to extensions in `ServerHello`, `HelloRetryRequest`, `EncryptedExtensions`, or `Certificate` that were not first offered in the corresponding request.

Important boundary checks:

- `supported_ekt_ciphers` is not listed as a mandatory-to-implement extension in [RFC 9846 Section 9.2](https://www.rfc-editor.org/rfc/rfc9846.html#section-9.2).
- The `illegal_parameter` placement rule is explicitly scoped to an extension the implementation recognizes and receives in a disallowed message.
- The ignore-unknown rule applies to a server receiving `ClientHello`, and to a client receiving `CertificateRequest` or `NewSessionTicket`. It does not authorize ignoring unknown extension responses in `EncryptedExtensions` or `Certificate`.

## Relevant Source Code

`ssl/statem/extensions.c:747`

```c
/* Unknown extension. We allow it */
*found = NULL;
return 1;
```

Unknown extension types are accepted by `verify_extension()` and reported to the caller as `thisex == NULL`.

`ssl/statem/extensions.c:958`

```c
if (!verify_extension(s, context, type, exts, raw_extensions, &thisex)
    || (thisex != NULL && thisex->present == 1)
    || (type == TLSEXT_TYPE_psk
        && (context & SSL_EXT_CLIENT_HELLO) != 0
        && PACKET_remaining(&extensions) != 0)) {
    SSLfatal(s, SSL_AD_ILLEGAL_PARAMETER, SSL_R_BAD_EXTENSION);
    goto err;
}
if (thisex == NULL)
    continue;
```

This skips unknown extensions before OpenSSL reaches the unsolicited-response check.

`ssl/statem/extensions.c:985`

```c
if (idx < OSSL_NELEM(ext_defs)
    && (context & (SSL_EXT_CLIENT_HELLO
                   | SSL_EXT_TLS1_3_CERTIFICATE_REQUEST
                   | SSL_EXT_TLS1_3_NEW_SESSION_TICKET)) == 0
    && type != TLSEXT_TYPE_cookie
    && type != TLSEXT_TYPE_renegotiate
    && type != TLSEXT_TYPE_signed_certificate_timestamp
    && (s->ext.extflags[idx] & SSL_EXT_FLAG_SENT) == 0) {
    SSLfatal(s, SSL_AD_UNSUPPORTED_EXTENSION,
             SSL_R_UNSOLICITED_EXTENSION);
    goto err;
}
```

The built-in unsolicited-response check only runs after an extension has matched an entry in `ext_defs`.

`ssl/statem/extensions.c:806`

```c
int tls_validate_no_unknown_extensions(SSL_CONNECTION *s, PACKET *packet,
    unsigned int context)
```

This helper rejects unknown extension types with `SSL_AD_UNSUPPORTED_EXTENSION`, and its local comment says it is used for TLS 1.3 server extension responses where unknown extensions are not ignored.

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
    goto err;
```

The TLS 1.3 `HelloRetryRequest` and `ServerHello` client paths explicitly reject unknown response extensions.

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

The TLS 1.3 `Certificate` and `EncryptedExtensions` paths collect and parse extensions, but do not call `tls_validate_no_unknown_extensions()`.

## Implementation Behavior

OpenSSL has no stock `supported_ekt_ciphers` entry, so extension type `39` is treated as unknown. In TLS 1.3 `Certificate` and `EncryptedExtensions`, the unknown type reaches `tls_collect_extensions()`, `verify_extension()` returns success with `thisex == NULL`, and the extension is skipped.

Because the extension is skipped as unknown, OpenSSL does not run the built-in unsolicited-response check. The client therefore accepts a response extension that was not requested in `ClientHello`.

For comparison, a recognized extension in a disallowed message is rejected. Injecting `supported_groups` type `10` into `Certificate` produces `illegal_parameter`, showing that the harness and code path do detect placement failures for recognized extensions.

## Inconsistency Reason

RFC 9846 separates two requirements:

- A recognized extension in a message where it is not specified must fail with `illegal_parameter`.
- A response extension in `ServerHello`, `EncryptedExtensions`, `HelloRetryRequest`, or `Certificate` that was not first offered must fail with `unsupported_extension`.

OpenSSL satisfies the first behavior for recognized extensions. It does not satisfy the second behavior for unknown or unsupported extension response types in `Certificate` and `EncryptedExtensions`.

The corrected issue framing is therefore not "OpenSSL lacks EKT support". It is: OpenSSL TLS 1.3 client accepts unrequested unknown response extension type `39` in `Certificate` and `EncryptedExtensions`.

## Runtime Evidence

The required rerun was executed against:

```text
OpenSSL 4.1.0-dev  (Library: OpenSSL 4.1.0-dev )
built on: Sun Aug  2 09:37:35 2026 UTC
platform: linux-x86_64
```

I reran three checks: type `39` in `Certificate`, type `39` in `EncryptedExtensions`, and a positive-control injection of recognized type `10` in `Certificate`. The observations are included below so the report does not depend on external log files.

### Reproducer: type 39 in Certificate

Command shape:

```sh
cd implementions/openssl-master
TOP=. PERL5LIB=util/perl perl ../../opt/runs/rfc9846/rfc9846-openssl/001-050/review-tools/unknown_certificate_extension_probe.pl 39
```

Key observations:

```text
MODIFY Certificate add extension 39
Protocol: TLSv1.3
EXT_TYPE 39
INJECTED 1
SUCCESS 1
TLS13 1
ALERT_SIDE server
ALERT_DESC 0
```

Observed stderr contained only certificate verification warnings and normal `close_notify` alerts, not a fatal `unsupported_extension` alert.

Result: OpenSSL accepted the modified TLS 1.3 `Certificate` and completed the handshake. Expected behavior is a fatal `unsupported_extension` abort.

### Cross-check: type 39 in EncryptedExtensions

Command shape:

```sh
cd implementions/openssl-master
TOP=. PERL5LIB=util/perl perl ../../opt/runs/rfc9846/rfc9846-openssl/001-050/review-tools/unknown_ee_extension_probe.pl 39
```

Key observations:

```text
MODIFY EncryptedExtensions add extension 39
Protocol: TLSv1.3
EXT_TYPE 39
INJECTED 1
SUCCESS 1
TLS13 1
ALERT_SIDE server
ALERT_DESC 0
```

`EncryptedExtensions` is an allowed message location for `supported_ekt_ciphers`, but the client did not request this extension. OpenSSL still completed the handshake, confirming the unrequested-response validation gap.

### Positive control: recognized type 10 in Certificate

Command shape:

```sh
cd implementions/openssl-master
TOP=. PERL5LIB=util/perl perl ../../opt/runs/rfc9846/rfc9846-openssl/001-050/review-tools/unknown_certificate_extension_probe.pl 10
```

Key observations:

```text
MODIFY Certificate add extension 10
Protocol: TLSv1.3
EXT_TYPE 10
INJECTED 1
SUCCESS 0
TLS13 1
ALERT_SIDE client
ALERT_DESC 47
```

Observed stderr for the control recorded `tls_collect_extensions:bad extension`, `fatal:illegal parameter`, and alert number `47`. This confirms the probe can observe a rejection when OpenSSL recognizes an extension and rejects its placement.

## Impact

A TLS 1.3 client using OpenSSL can accept server response extensions that were never requested when those response extensions use unknown or unsupported extension types in `Certificate` or `EncryptedExtensions`. This weakens TLS 1.3 extension negotiation invariants and can hide non-compliant server behavior from applications and test suites.

The observed behavior does not prove that OpenSSL implements EKT incorrectly. It proves that the client response validation path accepts an unrequested unsupported extension response.

## Fix Direction

Add unknown or unrequested response-extension validation for TLS 1.3 `Certificate` and `EncryptedExtensions`, aligned with the existing `ServerHello` and `HelloRetryRequest` handling.

The fix should preserve the RFC 9846 exceptions for extension-bearing messages where unknown extensions are explicitly ignored, such as `CertificateRequest` and `NewSessionTicket`, and should continue to reject recognized extensions in invalid message contexts with `illegal_parameter`.
