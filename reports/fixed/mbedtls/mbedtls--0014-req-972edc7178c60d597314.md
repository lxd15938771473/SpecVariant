# RFC9846 mbedTLS issue: `psk_key_exchange_modes` accepts trailing extension data

## Summary

mbedTLS processes the implemented TLS 1.3 `psk_key_exchange_modes` extension but accepts an extension payload that contains a valid `PskKeyExchangeModes` structure followed by an extra byte. RFC9846 requires the receiver to abort with `decode_error` when an implemented, non-ignored extension leaves bytes after its structure is parsed.

## Standard Requirement

- Standard: [RFC 9846](https://www.rfc-editor.org/rfc/rfc9846.html)
- Official section: [RFC 9846, Section 4.3, Extensions](https://www.rfc-editor.org/rfc/rfc9846.html#section-4.3)
- Section: 4.3, Extensions
- Related extension structure: [RFC 9846, Section 4.3.9, Pre-Shared Key Exchange Modes](https://www.rfc-editor.org/rfc/rfc9846.html#section-4.3.9)

Relevant normative phrase:

```text
receivers MUST abort the handshake with a "decode_error" alert
```

RFC9846 Section 4.3 states that, unless an extension-specific rule says otherwise, trailing data in `extension_data` is forbidden. The receiver-side rule applies when the receiver actually processes an implemented extension; the only stated exception is for an extension that the receiver does not implement or is configured to ignore.

RFC9846 Section 4.3.9 defines `psk_key_exchange_modes` as:

```text
struct {
    PskKeyExchangeMode ke_modes<1..255>;
} PskKeyExchangeModes;
```

No exception allows bytes after the `ke_modes` vector. Therefore, a ClientHello extension payload such as `{ 1, psk_dhe_ke, 0xaa }` is malformed: the first two bytes form a valid structure, and `0xaa` is trailing data that must cause `decode_error`.

## Relevant Source Code

### `library/ssl_misc.h:134-157`

```c
#define MBEDTLS_SSL_TLS1_3_ALLOWED_EXTS_OF_CH                                  \
    (MBEDTLS_SSL_EXT_MASK(SERVERNAME)                             | \
     MBEDTLS_SSL_EXT_MASK(MAX_FRAGMENT_LENGTH)                    | \
     MBEDTLS_SSL_EXT_MASK(STATUS_REQUEST)                         | \
     MBEDTLS_SSL_EXT_MASK(SUPPORTED_GROUPS)                       | \
     MBEDTLS_SSL_EXT_MASK(SIG_ALG)                                | \
     MBEDTLS_SSL_EXT_MASK(USE_SRTP)                               | \
     MBEDTLS_SSL_EXT_MASK(HEARTBEAT)                              | \
     MBEDTLS_SSL_EXT_MASK(ALPN)                                   | \
     MBEDTLS_SSL_EXT_MASK(SCT)                                    | \
     MBEDTLS_SSL_EXT_MASK(CLI_CERT_TYPE)                          | \
     MBEDTLS_SSL_EXT_MASK(SERV_CERT_TYPE)                         | \
     MBEDTLS_SSL_EXT_MASK(PADDING)                                | \
     MBEDTLS_SSL_EXT_MASK(KEY_SHARE)                              | \
     MBEDTLS_SSL_EXT_MASK(PRE_SHARED_KEY)                         | \
     MBEDTLS_SSL_EXT_MASK(PSK_KEY_EXCHANGE_MODES)                 | \
     MBEDTLS_SSL_EXT_MASK(EARLY_DATA)                             | \
     MBEDTLS_SSL_EXT_MASK(COOKIE)                                 | \
     MBEDTLS_SSL_EXT_MASK(SUPPORTED_VERSIONS)                     | \
     MBEDTLS_SSL_EXT_MASK(CERT_AUTH)                              | \
     MBEDTLS_SSL_EXT_MASK(POST_HANDSHAKE_AUTH)                    | \
     MBEDTLS_SSL_EXT_MASK(SIG_ALG_CERT)                           | \
     MBEDTLS_SSL_EXT_MASK(RECORD_SIZE_LIMIT)                      | \
     MBEDTLS_SSL_TLS1_3_EXT_MASK_UNRECOGNIZED)
```

`PSK_KEY_EXCHANGE_MODES` is allowed in ClientHello, so this extension is not treated as an unimplemented or ignored extension on this path.

### `library/ssl_tls13_server.c:1470-1505`

```c
while (p < extensions_end) {
    unsigned int extension_type;
    size_t extension_data_len;
    const unsigned char *extension_data_end;

    MBEDTLS_SSL_CHK_BUF_READ_PTR(p, extensions_end, 4);
    extension_type = MBEDTLS_GET_UINT16_BE(p, 0);
    extension_data_len = MBEDTLS_GET_UINT16_BE(p, 2);
    p += 4;

    MBEDTLS_SSL_CHK_BUF_READ_PTR(p, extensions_end, extension_data_len);
    extension_data_end = p + extension_data_len;

    ret = mbedtls_ssl_tls13_check_received_extension(
        ssl, MBEDTLS_SSL_HS_CLIENT_HELLO, extension_type,
        allowed_exts);
```

The ClientHello extension loop passes the full `extension_data` range to the selected parser, but it does not track how many bytes the parser consumed.

### `library/ssl_tls13_server.c:1578-1591`

```c
case MBEDTLS_TLS_EXT_PSK_KEY_EXCHANGE_MODES:
    MBEDTLS_SSL_DEBUG_MSG(
        3, ("found psk key exchange modes extension"));

    ret = ssl_tls13_parse_key_exchange_modes_ext(
        ssl, p, extension_data_end);
    if (ret != 0) {
        MBEDTLS_SSL_DEBUG_RET(
            1, "ssl_tls13_parse_key_exchange_modes_ext", ret);
        return ret;
    }

    break;
```

`psk_key_exchange_modes` is dispatched to a dedicated parser. This confirms that RFC9846's "does not implement or is configured to ignore" exception does not apply.

### `library/ssl_tls13_server.c:108-148`

```c
static int ssl_tls13_parse_key_exchange_modes_ext(mbedtls_ssl_context *ssl,
                                                  const unsigned char *buf,
                                                  const unsigned char *end)
{
    const unsigned char *p = buf;
    size_t ke_modes_len;
    int ke_modes = 0;

    MBEDTLS_SSL_CHK_BUF_READ_PTR(p, end, 1);
    ke_modes_len = *p++;

    if (ke_modes_len > 2) {
        MBEDTLS_SSL_PEND_FATAL_ALERT(MBEDTLS_SSL_ALERT_MSG_ILLEGAL_PARAMETER,
                                     MBEDTLS_ERR_SSL_ILLEGAL_PARAMETER);
        return MBEDTLS_ERR_SSL_HANDSHAKE_FAILURE;
    }

    MBEDTLS_SSL_CHK_BUF_READ_PTR(p, end, ke_modes_len);

    while (ke_modes_len-- != 0) {
        switch (*p++) {
            case MBEDTLS_SSL_TLS1_3_PSK_MODE_PURE:
                ke_modes |= MBEDTLS_SSL_TLS1_3_KEY_EXCHANGE_MODE_PSK;
                break;
            case MBEDTLS_SSL_TLS1_3_PSK_MODE_ECDHE:
                ke_modes |= MBEDTLS_SSL_TLS1_3_KEY_EXCHANGE_MODE_PSK_EPHEMERAL;
                break;
            default:
                MBEDTLS_SSL_PEND_FATAL_ALERT(MBEDTLS_SSL_ALERT_MSG_ILLEGAL_PARAMETER,
                                             MBEDTLS_ERR_SSL_ILLEGAL_PARAMETER);
                return MBEDTLS_ERR_SSL_ILLEGAL_PARAMETER;
        }
    }

    ssl->handshake->tls13_kex_modes = ke_modes;
    return 0;
}
```

The parser checks that enough bytes exist for the advertised vector, but it does not check that `p == end` before returning success.

### `library/ssl_tls13_server.c:1660-1662`

```c
p += extension_data_len;
```

After the parser returns, the caller skips the entire declared extension length. It does not detect that the parser accepted a valid internal structure plus leftover bytes.

## Implementation Behavior

For `psk_key_exchange_modes`, mbedTLS:

- accepts the extension as valid for ClientHello;
- dispatches it to `ssl_tls13_parse_key_exchange_modes_ext`;
- reads only the `ke_modes` vector declared by the first byte;
- stores the parsed modes and returns success;
- never verifies that no bytes remain between the parser cursor and `extension_data_end`.

This means the receiver continues the handshake for malformed extension data that RFC9846 requires to fail with `decode_error`.

## Inconsistency Reason

RFC9846 requires receivers to abort with `decode_error` when an implemented, processed extension leaves data after the extension-specific structure is parsed. mbedTLS processes `psk_key_exchange_modes`, parses the valid `ke_modes` vector, and returns success even when one byte remains in the extension payload. The caller does not perform a secondary exact-consumption check, so the malformed extension is accepted.

## Runtime Evidence

Runtime verification was performed in two layers. The first test called the dedicated parser directly with a valid payload and then with the same payload plus one trailing byte. The second test fed complete TLS records containing those two ClientHello variants through the public server handshake path.

### Parser-level probe

The parser was called twice:

- control input: `{ 1, MBEDTLS_SSL_TLS1_3_PSK_MODE_ECDHE }`
- mutated input: `{ 1, MBEDTLS_SSL_TLS1_3_PSK_MODE_ECDHE, 0xaa }`

Observed output:

```text
psk_key_exchange_modes ok_ret=0 trailing_ret=0
```

The parser test exited with code `0`.

The control parse succeeds, as expected. The mutated parse also succeeds (`trailing_ret=0`), which demonstrates that the trailing byte is accepted instead of causing a `decode_error`.

### Full ClientHello path probe

The wire-path test constructed two complete TLS records:

- control ClientHello: `psk_key_exchange_modes` extension payload `01 01`
- malformed ClientHello: same extension payload with one trailing byte, `01 01 aa`

Observed output:

```text
ok step0_ret=0 step1_ret=0 state=18 in_pos=87 out_len=0 send_alert=0 alert_type=0 alert_reason=0 err=
trailing step0_ret=0 step1_ret=0 state=18 in_pos=88 out_len=0 send_alert=0 alert_type=0 alert_reason=0 err=
expected_decode_error=-29440 expected_decode_alert=50 ok_ret=0 trailing_ret=0
```

The wire-path test exited with code `1`; that result was expected because the test intentionally returns nonzero when the malformed ClientHello is accepted instead of returning `MBEDTLS_ERR_SSL_DECODE_ERROR`.

The malformed ClientHello reaches the same post-ClientHello state as the control input (`state=18`, `MBEDTLS_SSL_SERVER_CCS_AFTER_SERVER_HELLO`) and no fatal alert is pending (`send_alert=0`, `alert_type=0`). This confirms that the full server-side ClientHello processing path accepts the trailing byte instead of aborting with `decode_error`.

## Impact

A peer can send a malformed TLS 1.3 ClientHello containing trailing bytes in an implemented extension and have mbedTLS accept that extension. This is a protocol conformance issue and may also create inconsistent behavior with stricter TLS 1.3 peers or tests that enforce exact extension parsing.

## Fix Direction

Add exact-consumption validation for implemented extension parsers. For `psk_key_exchange_modes`, return `MBEDTLS_ERR_SSL_DECODE_ERROR` and pend `MBEDTLS_SSL_ALERT_MSG_DECODE_ERROR` if `p != end` after parsing `ke_modes`.

The broader fix should audit other TLS 1.3 extension parsers that receive `(buf, end)` and parse an inner length-delimited structure, because the dispatch layer does not currently verify parser consumption.
