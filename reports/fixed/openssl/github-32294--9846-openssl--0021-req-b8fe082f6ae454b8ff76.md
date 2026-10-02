# RFC 9846 issue found: unrequested transparency_info accepted in EncryptedExtensions

## Decision

- Latest runtime recheck: `2026-08-10`

OpenSSL accepts a TLS 1.3 `EncryptedExtensions` message containing extension type 52 (`transparency_info`). RFC 9846 only permits `transparency_info` in `ClientHello`, `CertificateRequest`, and `Certificate`. Because `EncryptedExtensions` is a TLS 1.3 extension response message, an extension that was not first requested by the peer must cause the endpoint to abort the handshake with `unsupported_extension`.

This is not a claim that OpenSSL is required to implement the optional `transparency_info` extension. The issue is narrower: when an unrequested response extension appears in TLS 1.3 `EncryptedExtensions`, OpenSSL ignores it and completes the handshake instead of rejecting the response message.

## Standard Requirement

Primary standard: [RFC 9846 Section 4.3](https://www.rfc-editor.org/rfc/rfc9846.html#section-4.3).

RFC 9846 defines TLS extension responses as the server extensions sent in `ServerHello`, `EncryptedExtensions`, `HelloRetryRequest`, and `Certificate`. It then requires implementations not to send such responses unless the remote endpoint sent the corresponding request, except for the `cookie` extension in `HelloRetryRequest`. Upon receiving such an extension, the endpoint must abort with `unsupported_extension`.

Relevant RFC 9846 requirements:

- [Section 4.3](https://www.rfc-editor.org/rfc/rfc9846.html#section-4.3) defines `transparency_info(52)` and lists `transparency_info` as allowed only in `CH, CR, CT`.
- [Section 4.3](https://www.rfc-editor.org/rfc/rfc9846.html#section-4.3) forbids unrequested extension responses and requires the receiver to abort with `unsupported_extension`.
- [Section 6.2](https://www.rfc-editor.org/rfc/rfc9846.html#section-6.2) defines `unsupported_extension` for extensions in `ServerHello`, `HelloRetryRequest`, `EncryptedExtensions`, or `Certificate` that were not first offered in the corresponding `ClientHello` or `CertificateRequest`.

Scoping check:

- The rule that clients ignore unrecognized extensions is stated for messages such as `CertificateRequest`; it is not a general permission to ignore arbitrary unrecognized extensions in TLS 1.3 `EncryptedExtensions`.
- The separate wrong-message rule for recognized extensions maps recognized-but-forbidden placements to `illegal_parameter`. That recognized-extension qualifier does not remove the Section 4.3 response-extension rule for unrequested extensions in `EncryptedExtensions`; the required alert for that response-extension case is `unsupported_extension`.

## Code Evidence

OpenSSL does not define a built-in `transparency_info` extension type. The nearby Certificate Transparency constant is the older SCT extension type 18:

```c
#define TLSEXT_TYPE_signed_certificate_timestamp 18
```

Evidence: `implementions/openssl-master/include/openssl/tls1.h:125`.

The TLS 1.3 constants include `signature_algorithms_cert(50)`, `key_share(51)`, and `quic_transport_parameters(57)`, but no built-in `transparency_info(52)` symbol.

Evidence: `implementions/openssl-master/include/openssl/tls1.h:161`.

OpenSSL has a helper that rejects unknown extension types in TLS 1.3 server response messages:

```c
int tls_validate_no_unknown_extensions(SSL_CONNECTION *s, PACKET *packet,
    unsigned int context)
```

The helper is documented as being for TLS 1.3 server extension responses where unknown extensions are not ignored, and it raises `SSL_AD_UNSUPPORTED_EXTENSION` for a type that is neither built-in nor registered as a custom extension.

Evidence: `implementions/openssl-master/ssl/statem/extensions.c:801`, `implementions/openssl-master/ssl/statem/extensions.c:849`.

The generic extension collection path allows unknown extensions:

```c
/* Unknown extension. We allow it */
*found = NULL;
return 1;
```

and later skips unknown extensions during collection:

```c
if (thisex == NULL)
    continue;
```

Evidence: `implementions/openssl-master/ssl/statem/extensions.c:747`, `implementions/openssl-master/ssl/statem/extensions.c:966`.

The TLS 1.3 `EncryptedExtensions` client path only collects and parses extensions:

```c
if (!tls_collect_extensions(s, &extensions,
        SSL_EXT_TLS1_3_ENCRYPTED_EXTENSIONS, &rawexts,
        NULL, 1)
    || !tls_parse_all_extensions(s, SSL_EXT_TLS1_3_ENCRYPTED_EXTENSIONS,
        rawexts, NULL, 0, 1)) {
    goto err;
}
```

Evidence: `implementions/openssl-master/ssl/statem/statem_clnt.c:4489`.

By contrast, OpenSSL calls `tls_validate_no_unknown_extensions()` while processing TLS 1.3 `HelloRetryRequest` and `ServerHello`.

Evidence:

- `implementions/openssl-master/ssl/statem/statem_clnt.c:1802`
- `implementions/openssl-master/ssl/statem/statem_clnt.c:1965`

## Runtime Evidence

Action: I ran the TLSProxy EncryptedExtensions probe from `implementions/openssl-master` and requested injection of extension type `52`.

Latest normalized command:

```sh
PERL5LIB=util/perl TOP=. \
perl ../../opt/runs/rfc9846/rfc9846-openssl/001-050/review-tools/unknown_ee_extension_probe.pl 52
```

Observed output:

```text
MODIFY EncryptedExtensions add extension 52
EXT_TYPE 52
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

Interpretation:

- The proxy injected extension type 52 into TLS 1.3 `EncryptedExtensions`.
- The injected extension was present (`INJECTED 1`).
- The TLS 1.3 handshake completed (`SUCCESS 1`, `TLS13 1`, `CONNECTION ESTABLISHED`).
- The only observed alert was normal `close_notify` (`ALERT_DESC 0`), not fatal `unsupported_extension`.

Expected result: the OpenSSL client aborts the handshake with `unsupported_extension`.

Observed result: the OpenSSL client accepts the injected `EncryptedExtensions` and completes the TLS 1.3 handshake.

## Why This Is An Issue

RFC 9846 restricts `transparency_info` to `ClientHello`, `CertificateRequest`, and `Certificate`. It also requires endpoints to abort with `unsupported_extension` when an extension response in `EncryptedExtensions` was not first offered in the corresponding request.

OpenSSL instead treats type 52 as an unknown extension in `EncryptedExtensions`, skips it during collection, and continues the TLS 1.3 handshake. The runtime probe confirms the externally visible behavior.

## Impact

A peer can send unrequested TLS extension response types in TLS 1.3 `EncryptedExtensions` without OpenSSL rejecting the message. This weakens RFC 9846 response-extension validation and allows non-compliant peers or intermediaries to carry extension data through a response message that should have been rejected.

## Fix Direction

Apply the same unknown response-extension validation to TLS 1.3 `EncryptedExtensions` that OpenSSL already applies to TLS 1.3 `ServerHello` and `HelloRetryRequest`. Concretely, after extracting the `EncryptedExtensions` extension block and before accepting it for parsing, call `tls_validate_no_unknown_extensions()` with `SSL_EXT_TLS1_3_ENCRYPTED_EXTENSIONS`, while preserving the existing built-in unsolicited-extension checks for recognized extension types.
