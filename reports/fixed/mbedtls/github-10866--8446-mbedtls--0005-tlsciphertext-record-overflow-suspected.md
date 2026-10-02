# Oversized TLSCiphertext records violate the 16640-byte limit and are reported as bad_record_mac instead of record_overflow

## Merge Note

This report is the canonical merged report for the former standalone
Section 5.2 length-cap note.

The former Section 5.2 note focused on the RFC 8446 length cap itself
("`TLSCiphertext.length` MUST NOT exceed `2^14 + 256`"), while this report
focuses on the receive-side error mapping ("oversized protected records MUST
trigger `record_overflow`"). In mbedTLS these are the same defect: there is no
pre-decrypt receive-side check for `TLSCiphertext.length > 16640`, so the
implementation also falls through to `bad_record_mac` instead of
`record_overflow`.

## Problem Description

RFC 8446 Section 5.2 couples two requirements for protected records:
`TLSCiphertext.length` itself must not exceed `2^14 + 256`, and an endpoint
that receives a record beyond that bound must terminate the connection with
`record_overflow`.

The dedicated post-handshake reproducer completed a TLS 1.3 handshake, replaced
the second client application-data record with a forged TLSCiphertext header
advertising length `16641`, and observed that mbedTLS accepted that wire length
into the receive/decrypt path and sent alert `20` (`bad_record_mac`) instead of
alert `22` (`record_overflow`).

This report deduplicates multiple candidate-level records that resolved to the same root cause.

## Standard Requirement

- Official standards: [RFC 8446](https://www.rfc-editor.org/rfc/rfc8446.html) and [RFC 9846](https://www.rfc-editor.org/rfc/rfc9846.html)

- Section: RFC 8446 Section 5.2 Record Payload Protection (lines 4498-4501, 4559-4560)

> The length MUST NOT exceed 2^14 + 256 bytes.  An endpoint that receives a
>       record that exceeds this length MUST terminate the connection with
>       a "record_overflow" alert.

Interpretation:

Applies when encoding or receiving a TLSCiphertext record. The implementation
must not generate or accept `TLSCiphertext.length > 16640`, and on receive it
must reject that condition based on the ciphertext-length bound itself and
terminate with `record_overflow`. Falling through to AEAD authentication
failure and reporting `bad_record_mac` is not equivalent to the RFC's required
length enforcement and error mapping.

Cross-check:

The obsoleting text in [RFC 9846](https://www.rfc-editor.org/rfc/rfc9846.html) keeps the same rule and alert mapping for oversized TLSCiphertext records, so this conclusion does not depend on an outdated wording in RFC 8446.

## Relevant Source Code

The stream TLS receive path parses the wire length, accepts it into the input buffer, attempts AEAD deprotection, maps authentication failure to `MBEDTLS_ERR_SSL_INVALID_MAC`, and explicitly sends `bad_record_mac`. There is no pre-decrypt TLS 1.3 ciphertext-length guard that maps `length > 16640` to `record_overflow`.

### `library/ssl_msg.c:3703-3716`

```c
    rec->data_offset = rec_hdr_len_offset + rec_hdr_len_len;
    rec->data_len    = MBEDTLS_GET_UINT16_BE(buf, rec_hdr_len_offset);
    MBEDTLS_SSL_DEBUG_BUF(4, "input record header", buf, rec->data_offset);

    MBEDTLS_SSL_DEBUG_MSG(3, ("input record: msgtype = %u, "
                              "version = [0x%x], msglen = %" MBEDTLS_PRINTF_SIZET,
                              rec->type, (unsigned) tls_version, rec->data_len));

    rec->buf     = buf;
    rec->buf_len = rec->data_offset + rec->data_len;

    if (rec->data_len == 0) {
        MBEDTLS_SSL_DEBUG_MSG(1, ("rejecting empty record"));
        return MBEDTLS_ERR_SSL_INVALID_RECORD;
```

Record-header parsing copies the advertised TLSCiphertext length from the wire and prepares to fetch that many bytes. The only immediate length rejection here is `0`; there is no `> 16640` TLS 1.3 overflow check.

### `library/ssl_tls.c:2165-2169`

```c
{
    { PSA_SUCCESS,                     0 },
    { PSA_ERROR_INSUFFICIENT_MEMORY,   MBEDTLS_ERR_SSL_ALLOC_FAILED },
    { PSA_ERROR_NOT_SUPPORTED,         MBEDTLS_ERR_SSL_FEATURE_UNAVAILABLE },
    { PSA_ERROR_INVALID_SIGNATURE,     MBEDTLS_ERR_SSL_INVALID_MAC },
```

AEAD authentication failure is normalized to `MBEDTLS_ERR_SSL_INVALID_MAC`.

### `library/ssl_msg.c:3835-3881`

```c
        if ((ret = mbedtls_ssl_decrypt_buf(ssl, ssl->transform_in,
                                           rec)) != 0) {
            MBEDTLS_SSL_DEBUG_RET(1, "ssl_decrypt_buf", ret);

            /*
             * The decryption of the record failed, no reason to ignore it,
             * return in error with the decryption error code.
             */
            return ret;
        }
```

Once the oversized ciphertext reaches the deprotection stage, the record layer returns the AEAD failure directly. There is still no conversion to `record_overflow`.

### `library/ssl_msg.c:4877-4882`

```c
            /* Error out (and send alert) on invalid records */
#if defined(MBEDTLS_SSL_ALL_ALERT_MESSAGES)
            if (ret == MBEDTLS_ERR_SSL_INVALID_MAC) {
                mbedtls_ssl_send_alert_message(ssl,
                                               MBEDTLS_SSL_ALERT_LEVEL_FATAL,
                                               MBEDTLS_SSL_ALERT_MSG_BAD_RECORD_MAC);
```

On the stream TLS path, `MBEDTLS_ERR_SSL_INVALID_MAC` is emitted to the peer specifically as fatal alert `bad_record_mac` (`20`).

## Runtime Evidence

### Round 1

- Status: `passed`
- Positive control: `passed`
- Reproducer: `passed`

The focused TLSCiphertext probe completed a full TLS 1.3 handshake, forwarded three client records, then replaced the next encrypted application-data record header `1703030050` with `1703034101`, which advertises ciphertext length `16641`. The server accepted the oversized length from the wire, attempted decryption, and failed with `Verification of the message MAC failed`. A debug rerun then captured `send alert level=2 message=20`, proving that the outbound alert was `bad_record_mac`, not `record_overflow`.

#### Step: tlsciphertext-oversized-record

- Status: `passed`
- Exit code: `0`
- Key observations:
  - `observed.injected = true`
  - `observed.injected_record_header_hex = 1703034101`
  - Server error: `Verification of the message MAC failed`

#### Step: debug-confirmation

- Status: `passed`
- Key observation: the server sent fatal alert `20` (`bad_record_mac`) after
  the injected oversized TLSCiphertext reached decryption.
  - The same debug log shows `input record: msgtype = 23 ... msglen = 16641` immediately before `psa_aead_decrypt() returned -29056 (-0x7180)`

## Inconsistency Reason

- For oversized TLSCiphertext input, mbedTLS does not enforce the RFC 8446 length bound before deprotection. It instead treats the forged record as an authentication failure and emits `bad_record_mac`.

## Decision Reason

- RFC 8446 requires `record_overflow` when `TLSCiphertext.length > 16640`, regardless of whether the ciphertext would also fail authentication.
- The implementation parses and fetches the oversized ciphertext, routes the failure through `PSA_ERROR_INVALID_SIGNATURE -> MBEDTLS_ERR_SSL_INVALID_MAC`, and explicitly sends alert `20`.
- The dedicated post-handshake reproducer exercised that exact path and the debug rerun captured the concrete alert send site, so this is a confirmed implementation issue rather than a suspected one.
