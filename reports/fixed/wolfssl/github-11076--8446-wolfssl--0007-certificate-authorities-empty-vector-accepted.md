# Confirmed mismatch in Limit CertificateAuthoritiesExtension\.authorities to 3 through 2^16\-1 encoded octets\.

## Verdict

- Verdict: `issue_found`
- Confidence: `high`
- Scope: This issue is reachable when CA-name support is compiled in via `OPENSSL_EXTRA`. The default audit builds checked on 2026-08-03 did not enable that path.
- Covered Record IDs: `cand-7083e445317d-baseline`, `cand-e10e83f8e069-boundary`, `cand-86395ae74c72-missing`
- Root Cause Key: `certificate-authorities-empty-vector-accepted`

## Problem Description

This candidate targeted the requirement to Limit CertificateAuthoritiesExtension\.authorities to 3 through 2^16\-1 encoded octets\.\. Rechecking the standard, the source, and a feature-enabled live build confirmed that the mismatch is real when the `certificate_authorities` code path is compiled in.

This report deduplicates multiple candidate-level records that resolved to the same root cause.

## Standard Requirement

- Official standard: [RFC 8446](https://www.rfc-editor.org/rfc/rfc8446.html)
- Requirement ID: `req-2d742a751c579353e220`
- Section: Appendix B Protocol Data Structures and Constant Values \(lines 7285\-7287\)

> struct {
>           DistinguishedName authorities&lt;3..2^16-1&gt;;
>       } CertificateAuthoritiesExtension;

Interpretation:

The implementation must Limit CertificateAuthoritiesExtension\.authorities to 3 through 2^16\-1 encoded octets\.\.

This lower bound is deliberate rather than an editorial accident. In the same RFC section, structures that permit empty vectors are written with a `0` lower bound, e.g. `OIDFilterExtension.filters<0..2^16-1>` in Section 4.2.5. By contrast, `CertificateAuthoritiesExtension.authorities<3..2^16-1>` excludes an empty encoded authorities vector.

## Relevant Source Code

The certificate\_authorities implementation allows an empty authorities vector, so the RFC 8446 lower bound of 3 encoded octets is not enforced end to end\.

### `src/tls.c:7705-7777`

```
static word16 TLSX_CA_Names_GetSize(void* data)
{
    WOLFSSL* ssl = (WOLFSSL*)data;
    WOLF_STACK_OF(WOLFSSL_X509_NAME)* names;
    word32 size = 0;

    /* Length of names */
    size += OPAQUE16_LEN;
    for (names = SSL_PRIORITY_CA_NAMES(ssl); names != NULL; names = names->next) {
        byte seq[MAX_SEQ_SZ];
        WOLFSSL_X509_NAME* name = names->data.name;

        if (name != NULL) {
            /* 16-bit length | SEQ | Len | DER of name */
            size += (word32)(OPAQUE16_LEN + SetSequence(name->rawLen, seq) +
                             name->rawLen);
            if (size > WOLFSSL_MAX_16BIT) {
                return 0;
            }
        }
    }
    return (word16)size;
}

static word16 TLSX_CA_Names_Write(void* data, byte* output)
{
    WOLFSSL* ssl = (WOLFSSL*)data;
    WOLF_STACK_OF(WOLFSSL_X509_NAME)* names;
    byte* len;

    /* Reserve space for the length value */
    len = output;
    output += OPAQUE16_LEN;
    for (names = SSL_PRIORITY_CA_NAMES(ssl); names != NULL; names = names->next) {
        byte seq[MAX_SEQ_SZ];
        WOLFSSL_X509_NAME* name = names->data.name;

        if (name != NULL) {
            c16toa((word16)name->rawLen +
                   (word16)SetSequence(name->rawLen, seq), output);
            output += OPAQUE16_LEN;
            output += SetSequence(name->rawLen, output);
            XMEMCPY(output, name->raw, name->rawLen);
            output += name->rawLen;
        }
    }
    /* Write the total length */
    c16toa((word16)(output - len - OPAQUE16_LEN), len);
    return (word16)(output - len);
}

static int TLSX_CA_Names_Parse(WOLFSSL *ssl, const byte* input,
                                  word16 length, byte isRequest)
{
    word16 extLen;

    (void)isRequest;

    wolfSSL_sk_X509_NAME_pop_free(ssl->peer_ca_names, NULL);
    ssl->peer_ca_names = wolfSSL_sk_X509_NAME_new(NULL);
    if (ssl->peer_ca_names == NULL)
        return MEMORY_ERROR;

    if (length < OPAQUE16_LEN)
        return BUFFER_ERROR;

    ato16(input, &extLen);
    input += OPAQUE16_LEN;
    length -= OPAQUE16_LEN;
    if (extLen != length)
        return BUFFER_ERROR;

    while (length) {
```

certificate\_authorities serializes a 16\-bit authorities vector and parses each DistinguishedName as a 16\-bit length\-prefixed DER name\.

### `src/tls.c:16450-16458`

```
    #if !defined(NO_CERTS) && !defined(WOLFSSL_NO_CA_NAMES)
        if (IsAtLeastTLSv1_3(ssl->version) &&
                SSL_PRIORITY_CA_NAMES(ssl) != NULL) {
            WOLFSSL_MSG("Adding certificate authorities extension");
            if ((ret = TLSX_Push(&ssl->extensions,
                    TLSX_CERTIFICATE_AUTHORITIES, ssl, ssl->heap)) != 0) {
                    return ret;
            }
        }
```

wolfSSL only adds certificate\_authorities when a CA\-name list is configured locally\.

Because the condition is `SSL_PRIORITY_CA_NAMES(ssl) != NULL`, this is a pointer-presence check, not a non-empty-list check. A non-NULL stack with zero entries is still emitted.

### `src/ssl_api_cert.c:1348-1379`, `src/x509.c:14874-14940`, and `src/ssl_api_cert.c:1551-1595`

The CA-list APIs take ownership of any caller-provided stack without validating that it contains at least one name:

- `wolfSSL_CTX_set0_CA_list()` and `wolfSSL_set0_CA_list()` store `names` directly.
- `wolfSSL_sk_X509_NAME_new(NULL)` creates a valid empty stack object with `num == 0`.
- `wolfSSL_load_client_CA_file()` starts by creating an empty stack and only pushes entries if certificates are actually read, so an empty non-NULL list is a representable API value rather than a synthetic memory corruption case.

### `tests/api.c:34357-34439` and `tests/api/test_tls_ext.c:831-980`, `1132-1210`

The upstream tests cover:

- successful transmission/parsing of non-empty CA-name lists; and
- rejection of certain malformed CA-name encodings.

They do not cover the boundary case where the authorities vector is syntactically well-formed but empty (`00 00` inner vector length). That gap is consistent with the missing lower-bound enforcement in the implementation.

### `wolfssl/internal.h:1005-1021`

The CA-name APIs and `certificate_authorities` support are compiled only when `OPENSSL_EXTRA` is enabled; otherwise `WOLFSSL_NO_CA_NAMES` is defined and the entire path is compiled out.

## Runtime Evidence

### Earlier wrapper caveat

The previously cited wrapper step for this record is source-backed only. For `pos == 1471` it writes a static summary and exits; it does not execute a live handshake.

### Actual runtime recheck on 2026-08-03

To verify the behavior on a build where this feature is actually present, I configured a feature-enabled build with `-DWOLFSSL_LIGHTY=yes`, which enabled `OPENSSL_EXTRA`, `OPENSSL_ALL`, and `HAVE_LIGHTY`. I then ran the reproducer in three modes.

#### Non-empty control

Command:

`certificate_authorities_empty_vector_repro.exe nonempty`

Observed stdout:

```json
{
  "mode": "nonempty",
  "handshake_ret": 0,
  "cert_cb_called": true,
  "configured_client_ca_list_present": true,
  "configured_client_ca_list_count": 1,
  "peer_ca_list_present": true,
  "peer_ca_list_count": 1
}
```

This confirms the baseline path: a non-empty configured CA-name list is transmitted and the peer receives one CA name.

#### Empty-list repro

Command:

`certificate_authorities_empty_vector_repro.exe empty`

Observed stdout:

```json
{
  "mode": "empty",
  "handshake_ret": 0,
  "cert_cb_called": true,
  "configured_client_ca_list_present": true,
  "configured_client_ca_list_count": 0,
  "peer_ca_list_present": true,
  "peer_ca_list_count": 0
}
```

This shows the defect directly: the configured CA-name list exists but is empty, the handshake still succeeds, and the peer receives a present-but-empty CA-name list instead of rejecting the extension or treating it as absent.

#### NULL-list control

Command:

`certificate_authorities_empty_vector_repro.exe null`

Observed stdout:

```json
{
  "mode": "null",
  "handshake_ret": 0,
  "cert_cb_called": true,
  "configured_client_ca_list_present": false,
  "configured_client_ca_list_count": -1,
  "peer_ca_list_present": false,
  "peer_ca_list_count": -1
}
```

This confirms the intended contrast: when the configured list is `NULL`, the handshake still reaches the certificate callback, but no `certificate_authorities` list is observed on the peer at all.

## Inconsistency Reason

- In a build where CA-name support is enabled, wolfSSL serializes an empty `certificate_authorities` vector as length `0` and the peer parses it into an empty CA-name stack instead of rejecting the `<3..2^16-1>` lower-bound violation.
- The corresponding `NULL` configuration path behaves differently and correctly suppresses the extension entirely, so the defect is specifically that an empty-but-present list is treated as sendable and acceptable.

## Decision Reason

- The RFC 8446 structure requires `authorities<3..2^16-1>`. Runtime controls show the implementation distinguishes `NULL` from non-`NULL`: `NULL` suppresses the extension entirely, while an empty configured CA-name stack is serialized as a zero-length authorities vector and accepted by the peer as an empty list. That is a real syntax-handling defect in the compiled `certificate_authorities` implementation, though it is feature-gated rather than present in builds where `OPENSSL_EXTRA` is disabled.
