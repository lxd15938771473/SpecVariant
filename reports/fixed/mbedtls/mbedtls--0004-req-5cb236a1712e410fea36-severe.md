# TLS 1.3 server selects CertificateVerify correctly but does not enforce `signature_algorithms` on certificate signatures when `signature_algorithms_cert` is absent

## Problem Description

When a TLS 1.3 client omits `signature_algorithms_cert`, RFC 8446 requires the server to apply the `signature_algorithms` extension both to `CertificateVerify` and to signatures appearing in certificates.

mbedTLS parses the client `signature_algorithms` list and uses it to choose a `CertificateVerify` algorithm and a compatible end-entity key type, but it does not enforce the same constraint on the certificate signature algorithm. In practice, if two otherwise usable server certificates are configured and the SHA-1-signed certificate appears first, the TLS 1.3 server still sends that SHA-1 certificate even when a SHA-256 alternative is available. The behavior was reproduced independently with both ECDSA and RSA certificate pairs.

This report deduplicates multiple candidate-level records that resolved to the same root cause.

## Standard Requirement

- Primary sources:
  - [RFC 8446](https://www.rfc-editor.org/rfc/rfc8446.html)
  - [RFC 8446](https://www.rfc-editor.org/rfc/rfc8446.html)
  - [RFC 8446](https://www.rfc-editor.org/rfc/rfc8446.html)

The relevant RFC 8446 requirements are:

- If `signature_algorithms_cert` is absent, `signature_algorithms` also applies to signatures appearing in certificates.
- All certificates provided by the server must use signature algorithms advertised by the client if the server is able to provide such a chain.
- If the server cannot provide a fully compatible chain, it may fall back, but that fallback must not use deprecated SHA-1 unless the client advertisement permits SHA-1.
- The `CertificateVerify` rule is separate: choosing a valid `CertificateVerify` algorithm does not relax the certificate-chain signature constraint.

The self-signed or trust-anchor exception does not apply here because the offending certificate is the end-entity server certificate, not a trust anchor.

## Relevant Source Code

The implementation parses `signature_algorithms`, uses it to pick the `CertificateVerify` algorithm and end-entity key, then writes the selected chain verbatim.

### `library/ssl_tls13_server.c:1627-1637`

```c
            case MBEDTLS_TLS_EXT_SIG_ALG:
                MBEDTLS_SSL_DEBUG_MSG(3, ("found signature_algorithms extension"));

                ret = mbedtls_ssl_parse_sig_alg_ext(
                    ssl, p, extension_data_end);
                if (ret != 0) {
                    MBEDTLS_SSL_DEBUG_RET(
                        1, "mbedtls_ssl_parse_sig_alg_ext", ret);
                    return ret;
                }
                break;
```

TLS 1.3 `ClientHello` parsing has a concrete case for `signature_algorithms`, but there is no corresponding enforcement path that applies the parsed list to certificate signatures.

### `library/ssl_tls13_server.c:1106-1177`

`ssl_tls13_pick_key_cert()` iterates over the received signature schemes and uses them to choose a `(key, cert)` pair whose public key can produce `CertificateVerify`. The checks are about leaf key compatibility and key-usage constraints; they do not reject a candidate because the certificate itself is signed with a disallowed algorithm such as SHA-1.

### `library/ssl_tls13_generic.c:780-831`

`ssl_tls13_write_certificate_body()` walks `mbedtls_ssl_own_cert(ssl)` and writes the selected chain as-is:

```c
    while (crt != NULL) {
        size_t cert_data_len = crt->raw.len;

        MBEDTLS_SSL_CHK_BUF_PTR(p, end, cert_data_len + 3 + 2);
        MBEDTLS_PUT_UINT24_BE(cert_data_len, p, 0);
        p += 3;

        memcpy(p, crt->raw.p, cert_data_len);
        p += cert_data_len;
        crt = crt->next;

        MBEDTLS_PUT_UINT16_BE(0, p, 0);
        p += 2;
    }
```

Once `ssl_tls13_pick_key_cert()` chooses a certificate list node, the chain is serialized without any additional check that the certificate signatures comply with the client's advertised algorithms.

### `library/ssl_tls.c:1634-1676`

`ssl_append_key_cert()` appends each configured key-certificate pair to the end of the linked list. Certificate registration order is therefore preserved and becomes the iteration order used by `ssl_tls13_pick_key_cert()`. Because selection stops at the first candidate whose public key can produce an allowed `CertificateVerify` signature, a SHA-1-signed leaf registered before a SHA-256-signed equivalent can win without its certificate signature being checked.

## Runtime Evidence

### Reproducer Setup

All runs below were executed on `2026-08-04` with the bundled mbedTLS TLS 1.3
sample client and server programs.

Client parameters:

```powershell
ssl_client2.exe `
  server_name=localhost `
  force_version=tls13 `
  debug_level=0 `
  sig_algs=ecdsa_secp256r1_sha256
```

This constrains the client advertisement to a single signature scheme, `ecdsa_secp256r1_sha256`.

### Positive Reproducer: SHA-1 Certificate First, SHA-256 Alternative Available

Server parameters:

```powershell
ssl_server2.exe `
  server_port=4449 `
  force_version=tls13 `
  debug_level=4 `
  crt_file=framework/data_files/server5-sha1.crt `
  key_file=framework/data_files/server5.key `
  crt_file2=framework/data_files/server5.crt `
  key_file2=framework/data_files/server5.key
```

The server received only `ecdsa_secp256r1_sha256` and selected that algorithm
for CertificateVerify. It nevertheless serialized the configured first
certificate, logging `signed using : ECDSA with SHA1`. The client then aborted
the TLS 1.3 handshake because the certificate used an unacceptable hash.

This is the decisive run: the server had a compatible SHA-256 certificate available, so the RFC 8446 fallback exception does not apply. Despite that, the implementation still sent the SHA-1 certificate because it appeared first in configuration order.

### Control Run: SHA-256 Certificate First

Server parameters:

```powershell
ssl_server2.exe `
  server_port=4447 `
  force_version=tls13 `
  debug_level=3 `
  crt_file=framework/data_files/server5.crt `
  key_file=framework/data_files/server5.key `
  crt_file2=framework/data_files/server5-sha1.crt `
  key_file2=framework/data_files/server5.key
```

With the SHA-256 certificate configured first, the peers negotiated TLS 1.3,
certificate verification succeeded, and the client reported that the peer
certificate was `signed using : ECDSA with SHA256`.

This control run shows that a compatible SHA-256 certificate is available and usable; the noncompliant behavior depends on selection order, not on the absence of a conforming chain.

### RSA Reproducer: SHA-1 Certificate First

A second focused rerun used two RSA end-entity certificates with the same
subject, private key, and trust anchor but different certificate-signature
hashes: one SHA-1 leaf and one SHA-256 leaf.

The client advertised only `rsa_pss_rsae_sha256` and `rsa_pkcs1_sha256`. The server selected an allowed `rsa_pss_rsae_sha256` algorithm for `CertificateVerify`, but certificate registration order still controlled which leaf certificate was sent.

Observed results:

1. With the SHA-1 leaf registered first, the server selected `rsa_pss_rsae_sha256` for `CertificateVerify` but logged the selected certificate as `RSA with SHA1`; the validating client rejected it with `The certificate is signed with an unacceptable hash.`
2. A direct probe that ignored certificate validation confirmed that the actual peer leaf had `peer_signature_hash: sha1`.
3. With the SHA-256 leaf registered first, the server selected `RSA with SHA-256` and the client completed certificate verification successfully.

The RSA run independently confirms the ECDSA result: the defect is in certificate-chain selection and is not tied to a single public-key family.

## Inconsistency Reason

- When `signature_algorithms_cert` is absent, the server enforces the client list for `CertificateVerify` and end-entity key compatibility, but not for the certificate signature algorithm itself.
- As a result, configuration order can cause a TLS 1.3 server to send a SHA-1-signed end-entity certificate even though the client advertised only SHA-256 and a compliant SHA-256 certificate is available. This was observed with both ECDSA and RSA leaves.

## Decision Reason

- The RFC text is explicit for the base case where `signature_algorithms_cert` is omitted.
- Independent ECDSA and RSA reproducers demonstrate concrete TLS 1.3 executions in which the server is able to provide a conforming SHA-256 certificate yet still sends a SHA-1 certificate.
- The control run shows that the SHA-256 alternative is actually usable, closing the previous uncertainty about the fallback-chain exception.
- The RSA direct probe confirms that the server actually transmitted the SHA-1-signed end-entity certificate rather than merely provoking an unrelated validation failure.

Therefore the prior `suspected_issue` assessment is upgraded to `issue_found`.

## Remaining Uncertainty

- None for this baseline root cause.

## Consolidation Record

This canonical report now covers the ECDSA and RSA variants formerly tracked separately under `req-e0ecd104923052e29fe1` and `req-5c2af792d117e8008887`. Their candidate IDs, requirement IDs, registration-order evidence, handshake controls, and direct peer-certificate observation have been retained above.
