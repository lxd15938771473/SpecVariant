# Confirmed issue in duplicate extension handling for TLS 1.3 extension blocks

## Problem Description

The implementation accepts a TLS 1.3 `ClientHello` whose extension block contains the same extension type more than once. A targeted runtime replay confirmed that duplicating `supported_versions` does not trigger a fatal alert and still causes the server to send a normal `ServerHello` flight.

This report deduplicates multiple candidate-level records that resolved to the same root cause.

## Standard Requirement

- Official standard: [RFC 8446](https://www.rfc-editor.org/rfc/rfc8446.html)

- Section: `RFC 8446`, Section 4.2 Extensions, lines 2077-2078

> There MUST NOT be more than one extension of the
> same type in a given extension block.

Interpretation:

The endpoint must reject an extension block that repeats an extension type. Continuing the handshake after accepting a duplicate extension violates the requirement.

Additional RFC error-handling context:

> Peers which receive a message which is syntactically correct but
> semantically invalid ... MUST terminate the connection with an
> "illegal_parameter" alert.

This report does not rely on a duplicate-extension-specific alert mapping. The key compliance point is that the handshake must not continue after a duplicate extension is accepted.

## Relevant Source Code

The TLS 1.3 receive path records extension presence as a bitmask but never checks whether the bit was already set before accepting the next extension of the same type.

### `library/ssl_tls13_generic.c:123-175`

```c
int mbedtls_ssl_tls13_is_supported_versions_ext_present_in_exts(
    mbedtls_ssl_context *ssl,
    const unsigned char *buf, const unsigned char *end,
    const unsigned char **supported_versions_data,
    const unsigned char **supported_versions_data_end)
{
    const unsigned char *p = buf;
    size_t extensions_len;
    const unsigned char *extensions_end;

    *supported_versions_data = NULL;
    *supported_versions_data_end = NULL;

    /* Case of no extension */
    if (p == end) {
        return 0;
    }

    MBEDTLS_SSL_CHK_BUF_READ_PTR(p, end, 2);
    extensions_len = MBEDTLS_GET_UINT16_BE(p, 0);
    p += 2;

    MBEDTLS_SSL_CHK_BUF_READ_PTR(p, end, extensions_len);
    extensions_end = p + extensions_len;

    while (p < extensions_end) {
        unsigned int extension_type;
        size_t extension_data_len;

        MBEDTLS_SSL_CHK_BUF_READ_PTR(p, extensions_end, 4);
        extension_type = MBEDTLS_GET_UINT16_BE(p, 0);
        extension_data_len = MBEDTLS_GET_UINT16_BE(p, 2);
        p += 4;
        MBEDTLS_SSL_CHK_BUF_READ_PTR(p, extensions_end, extension_data_len);

        if (extension_type == MBEDTLS_TLS_EXT_SUPPORTED_VERSIONS) {
            *supported_versions_data = p;
            *supported_versions_data_end = p + extension_data_len;
            return 1;
        }
        p += extension_data_len;
    }

    return 0;
}
```

The pre-scan helper stops at the first `supported_versions` occurrence and does not check whether a second one appears later in the same extension block.

### `library/ssl_tls13_generic.c:1639-1684`

```c
int mbedtls_ssl_tls13_check_received_extension(
    mbedtls_ssl_context *ssl,
    int hs_msg_type,
    unsigned int received_extension_type,
    uint32_t hs_msg_allowed_extensions_mask)
{
    uint32_t extension_mask = mbedtls_ssl_get_extension_mask(
        received_extension_type);

    MBEDTLS_SSL_PRINT_EXT(
        3, hs_msg_type, received_extension_type, "received");

    if ((extension_mask & hs_msg_allowed_extensions_mask) == 0) {
        MBEDTLS_SSL_PRINT_EXT(
            3, hs_msg_type, received_extension_type, "is illegal");
        MBEDTLS_SSL_PEND_FATAL_ALERT(
            MBEDTLS_SSL_ALERT_MSG_ILLEGAL_PARAMETER,
            MBEDTLS_ERR_SSL_ILLEGAL_PARAMETER);
        return MBEDTLS_ERR_SSL_ILLEGAL_PARAMETER;
    }

    ssl->handshake->received_extensions |= extension_mask;
    switch (hs_msg_type) {
        case MBEDTLS_SSL_HS_SERVER_HELLO:
        case MBEDTLS_SSL_TLS1_3_HS_HELLO_RETRY_REQUEST:
        case MBEDTLS_SSL_HS_ENCRYPTED_EXTENSIONS:
        case MBEDTLS_SSL_HS_CERTIFICATE:
            if ((ssl->handshake->sent_extensions & extension_mask) != 0) {
                return 0;
            }
            break;
        default:
            return 0;
    }

    MBEDTLS_SSL_PRINT_EXT(
        3, hs_msg_type, received_extension_type, "is unsupported");
    MBEDTLS_SSL_PEND_FATAL_ALERT(
        MBEDTLS_SSL_ALERT_MSG_UNSUPPORTED_EXT,
        MBEDTLS_ERR_SSL_UNSUPPORTED_EXTENSION);
    return MBEDTLS_ERR_SSL_UNSUPPORTED_EXTENSION;
}
```

`received_extensions |= extension_mask` records that the type has appeared, but there is no branch that rejects a second appearance of the same type.

### `library/ssl_tls13_server.c:1358-1375`

```c
ret = mbedtls_ssl_tls13_is_supported_versions_ext_present_in_exts(
    ssl, p + 1 + p[0], end,
    &supported_versions_data, &supported_versions_data_end);

if (ret == 1) {
    ret = ssl_tls13_parse_supported_versions_ext(ssl,
                                                 supported_versions_data,
                                                 supported_versions_data_end);
    if (ret < 0) {
        return ret;
    }
}
```

The TLS 1.3 server path decides whether the message is a TLS 1.3 `ClientHello` by parsing the first `supported_versions` instance found in the extension block.

### `library/ssl_tls13_server.c:1470-1609`

```c
while (p < extensions_end) {
    unsigned int extension_type;
    size_t extension_data_len;
    const unsigned char *extension_data_end;
    uint32_t allowed_exts = MBEDTLS_SSL_TLS1_3_ALLOWED_EXTS_OF_CH;

    if (handshake->received_extensions & MBEDTLS_SSL_EXT_MASK(PRE_SHARED_KEY)) {
        MBEDTLS_SSL_PEND_FATAL_ALERT(
            MBEDTLS_SSL_ALERT_MSG_ILLEGAL_PARAMETER,
            MBEDTLS_ERR_SSL_ILLEGAL_PARAMETER);
        return MBEDTLS_ERR_SSL_ILLEGAL_PARAMETER;
    }

    MBEDTLS_SSL_CHK_BUF_READ_PTR(p, extensions_end, 4);
    extension_type = MBEDTLS_GET_UINT16_BE(p, 0);
    extension_data_len = MBEDTLS_GET_UINT16_BE(p, 2);
    p += 4;

    MBEDTLS_SSL_CHK_BUF_READ_PTR(p, extensions_end, extension_data_len);
    extension_data_end = p + extension_data_len;

    ret = mbedtls_ssl_tls13_check_received_extension(
        ssl, MBEDTLS_SSL_HS_CLIENT_HELLO, extension_type,
        allowed_exts);
    if (ret != 0) {
        return ret;
    }

    switch (extension_type) {
        case MBEDTLS_TLS_EXT_SUPPORTED_VERSIONS:
            /* Already parsed */
            break;
```

Once the server enters the main extension loop, a duplicated `supported_versions` entry is not rejected. The first one has already driven TLS 1.3 negotiation, and subsequent ones only pass through the allow-list check.

## Runtime Evidence

### Round 1

- Status: `completed`
- Positive control: `passed`
- Reproducer: `issue_reproduced`

Setup:

1. Capture a real TLS 1.3 `ClientHello` emitted by `ssl_client2.exe`.
2. Parse the captured record and duplicate only the `supported_versions` extension.
3. Replay both the original and modified records into `ssl_server2.exe`.
4. Compare the server's first response record and handshake progression.

Captured control extension list:

- `server_name`
- `supported_versions`
- `key_share`
- `psk_key_exchange_modes`
- `supported_groups`
- `signature_algorithms`

Reproducer extension list:

- `server_name`
- `supported_versions`
- `supported_versions`
- `key_share`
- `psk_key_exchange_modes`
- `supported_groups`
- `signature_algorithms`

Observed control response:

- Sent bytes: `218`
- Received bytes: `937`
- First record type: `22` (`Handshake`)
- First handshake type: `2` (`ServerHello`)
- Record sequence: `22, 20, 23, 23, 23, 23`

Observed duplicate-extension response:

- Sent bytes: `225`
- Received bytes: `937`
- First record type: `22` (`Handshake`)
- First handshake type: `2` (`ServerHello`)
- Record sequence: `22, 20, 23, 23, 23, 23`

Interpretation:

The duplicate-extension `ClientHello` did not trigger a fatal alert. The server accepted the message far enough to produce a normal TLS 1.3 `ServerHello` flight, which confirms that the duplicate `supported_versions` extension was not rejected.

## Why This Is a Real Issue

- The RFC forbids duplicate extension types in a single extension block.
- The implementation never checks whether an allowed extension type has already appeared.
- The runtime reproducer shows the handshake continuing after a duplicated `supported_versions` extension is injected.
- Therefore the implementation accepts behavior that the standard forbids.

## Inconsistency Reason

- Duplicate extension types are accepted because `mbedtls_ssl_tls13_check_received_extension()` only checks message allow-list membership and then ORs the type into `received_extensions` without a duplicate-presence check.

## Decision Reason

- Standard review confirms that duplicate extension types in one block are forbidden.
- Code review shows no duplicate-extension rejection on the TLS 1.3 `ClientHello` receive path.
- Runtime replay confirms that a `ClientHello` containing two `supported_versions` extensions still elicits a normal `ServerHello`.
- The combined evidence is sufficient to classify this report as `issue_found`.

## Remaining Uncertainty

- This report directly confirms the `ClientHello` plus duplicated `supported_versions` case.
- The same root cause likely affects other TLS 1.3 message/extension combinations that rely on the same shared validator, but those combinations were not individually replayed in this report.
