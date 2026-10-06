# Corrupted accepted 0-RTT data does not produce the required bad_record_mac alert

## Problem Description

After wolfSSL accepts TLS 1.3 early data, a corrupted 0-RTT application_data record is treated as ignorable early data instead of causing the required fatal bad_record_mac alert. The focused memio test first minted a session ticket that permits 0-RTT, then resumed with early data, flipped one byte in the first encrypted client application_data record before the server decrypted it, and checked the server's transmitted alert history. wolfSSL returned a fatal error to the API but did not transmit or record bad_record_mac.

## Standard Requirement

- Official standard: [RFC 8446](https://www.rfc-editor.org/rfc/rfc8446.html)
- Section: 4.2.10. Early Data Indication
- Section link: [RFC 8446 Section 4.2.10](https://www.rfc-editor.org/rfc/rfc8446.html#section-4.2.10)

> If the server chooses to accept the "early_data" extension, then it
>    MUST comply with the same error-handling requirements specified for
>    all records when processing early data records.  Specifically, if the
>    server fails to decrypt a 0-RTT record following an accepted
>    "early_data" extension, it MUST terminate the connection with a
>    "bad_record_mac" alert as per Section 5.2.

Interpretation:

Once the server accepts the TLS 1.3 `early_data` extension, a decryption failure on a 0-RTT record is not optional recovery behavior. The server must terminate the connection with a `bad_record_mac` alert.

## Relevant Source Code

wolfSSL accepts TLS 1.3 early data when the ClientHello contains `early_data`, the server has a nonzero early-data limit, the selected ticket is usable, and the server derives early-data decrypt keys. The record processing path then has a special TLS 1.3 early-data branch on decrypt failure that returns before the generic `bad_record_mac` alert is sent.

### `src/tls13.c:6906-6935`

```c
        extEarlyData = TLSX_Find(ssl->extensions, TLSX_EARLY_DATA);
        if (extEarlyData != NULL) {
            /* Check if accepting early data and first PSK.
             * RFC 9973: early_data is not compatible with
             * cert_with_extern_psk, so skip key derivation in that case. */
            if (ssl->earlyData != no_early_data && first
                && ssl->options.maxEarlyDataSz > 0
    #ifdef WOLFSSL_CERT_WITH_EXTERN_PSK
                && !hasCertWithExternPsk
    #endif
    #if defined(HAVE_SESSION_TICKET) && !defined(NO_SESSION_CACHE)
                /* RFC 8446 section 8: evict the session from the cache.
                 * Accept 0-RTT only when the eviction found the entry
                 * (single-use). */
                && wolfSSL_SSL_CTX_remove_session(ssl->ctx, ssl->session)
                    == 1
    #endif
            ) {
                extEarlyData->resp = 1;

                /* Derive early data decryption key. */
                ret = DeriveTls13Keys(ssl, early_data_key, DECRYPT_SIDE_ONLY,
                                                                             1);
                if (ret != 0)
                    return ret;
                if ((ret = SetKeysSide(ssl, DECRYPT_SIDE_ONLY)) != 0)
                    return ret;

                ssl->keys.encryptionOn = 1;
                ssl->earlyData = process_early_data;
```

This is the server acceptance path: `extEarlyData->resp = 1` means the server will signal accepted early data, and `ssl->earlyData = process_early_data` puts the server into accepted 0-RTT processing with early-data decrypt keys installed.

### `src/internal.c:24583-24625`

```c
                else {
                    WOLFSSL_MSG("Decrypt failed");
                #ifdef WOLFSSL_DTLS
                    /* If in DTLS mode, if the decrypt fails for any
                     * reason, pretend the datagram never happened. */
                    if (ssl->options.dtls) {
                        DropAndRestartProcessReply(ssl);
                        return HandleDTLSDecryptFailed(ssl);
                    }
                #endif /* WOLFSSL_DTLS */
                #ifdef WOLFSSL_EARLY_DATA
                    if (ssl->options.tls1_3) {
                         if (ssl->options.side == WOLFSSL_SERVER_END &&
                                  ssl->earlyData != no_early_data &&
                                  ssl->options.clientState <
                                                      CLIENT_FINISHED_COMPLETE) {
                            ssl->earlyDataSz += ssl->curSize;
                            if (ssl->earlyDataSz <=
                                                  ssl->options.maxEarlyDataSz) {
                                WOLFSSL_MSG("Ignoring EarlyData!");
                                if (ssl->keys.peer_sequence_number_lo-- == 0)
                                    ssl->keys.peer_sequence_number_hi--;
                                ssl->options.processReply = doProcessInit;
                                ssl->buffers.inputBuffer.idx += ssl->curSize;
                                if (ssl->buffers.inputBuffer.idx >
                                    ssl->buffers.inputBuffer.length) {
                                    WOLFSSL_ERROR(BUFFER_E);
                                    return BUFFER_E;
                                }

                                return 0;
                            }
                            WOLFSSL_MSG("Too much EarlyData!");
                            SendAlert(ssl, alert_fatal, unexpected_message);
                            WOLFSSL_ERROR(TOO_MUCH_EARLY_DATA);
                            return TOO_MUCH_EARLY_DATA;
                        }
                    }
                #endif
                    SendAlert(ssl, alert_fatal, bad_record_mac);
                    /* Push error once we know that we will error out here */
                    WOLFSSL_ERROR(ret);
                    return ret;
```

For a TLS 1.3 server still before `CLIENT_FINISHED_COMPLETE`, any decrypt failure on an early-data record within `maxEarlyDataSz` takes the `Ignoring EarlyData!` branch and returns `0` before reaching the unconditional `SendAlert(ssl, alert_fatal, bad_record_mac)` path. This contradicts RFC 8446 once the server has accepted the `early_data` extension.

### `tests/api/test_tls13.c:5247-5295`

```c
    /* Mint a ticket that permits 0-RTT. */
    ExpectIntEQ(test_memio_setup(&test_ctx, &ctx_c, &ctx_s, &ssl_c, &ssl_s,
                    wolfTLSv1_3_client_method, wolfTLSv1_3_server_method),
                0);
    ExpectIntGE(wolfSSL_CTX_set_max_early_data(ctx_s, MAX_EARLY_DATA_SZ), 0);
    ExpectIntGE(wolfSSL_set_max_early_data(ssl_s, MAX_EARLY_DATA_SZ), 0);
    ExpectIntEQ(test_memio_do_handshake(ssl_c, ssl_s, 10, NULL), 0);
    ExpectIntEQ(wolfSSL_read(ssl_c, msgBuf, (int)sizeof(msgBuf)), -1);
    ExpectIntEQ(wolfSSL_get_error(ssl_c, -1), WOLFSSL_ERROR_WANT_READ);
    ExpectNotNull(sess = wolfSSL_get1_session(ssl_c));
...
    ExpectIntEQ(test_tls13_early_data_write_until_write_ok(ssl_c, earlyMsg,
                    (int)sizeof(earlyMsg), &written), (int)sizeof(earlyMsg));
    ExpectIntEQ(written, (int)sizeof(earlyMsg));

    /* Corrupt the first encrypted application_data record in the client's
     * outbound buffer before the server attempts to decrypt it. */
...
    if (EXPECT_SUCCESS())
        test_ctx.s_buff[fuzzOff] ^= 0x01;

    ExpectIntEQ(test_tls13_early_data_read_until_write_ok(ssl_s, msgBuf,
                    (int)sizeof(msgBuf), &read), WOLFSSL_FATAL_ERROR);
    ExpectIntEQ(wolfSSL_get_alert_history(ssl_s, &h), WOLFSSL_SUCCESS);
    ExpectIntEQ(h.last_tx.code, bad_record_mac);
    ExpectIntEQ(h.last_tx.level, alert_fatal);
```

The focused test uses wolfSSL's in-memory I/O harness, not an abstract adapter: it creates a real TLS 1.3 ticket allowing 0-RTT, resumes with early data, mutates the encrypted record bytes in the client-to-server buffer, and then asserts that the server sent fatal `bad_record_mac`.

## Runtime Evidence

### Round 1

- Earlier native handshake probe: positive control passed, but the abstract mutation probe could not inject raw TLS 1.3 bytes and was not decisive for this issue.
- Follow-up focused test: added and ran `test_tls13_early_data_bad_record_mac` in wolfSSL's TLS 1.3 API test group.
- Relevant build features: `WOLFSSL_TLS13`, `WOLFSSL_EARLY_DATA`, and `HAVE_SESSION_TICKET` were enabled.
- Positive control inside test: the test first completes a TLS 1.3 handshake and obtains a session ticket with nonzero early-data allowance, then successfully calls `wolfSSL_write_early_data()` on the resumed client path. This verifies that the reproducer reached the real 0-RTT path rather than only a synthetic record parser.
- Mutation: the test locates the first encrypted client `application_data` record in memio and flips one byte at the start of that record body before the server reads it.
- Expected result: server returns fatal error and records/sends fatal `bad_record_mac` alert code `20`.
- Observed result from the saved run: `tests/api/test_tls13.c line 4891 failed`, `expected: h.last_tx.code == bad_record_mac`, `result: -1 != 20`. Exit code was `1`.

### Round 2

- Recheck action: rebuilt the current unit-test binary and ran only `test_tls13_early_data_bad_record_mac`.
- Recheck result: exit code `1`; the same assertion failed at the current source line `tests/api/test_tls13.c:5294`.
- Recheck stdout excerpt: `expected: h.last_tx.code == bad_record_mac`; `result: -1 != 20`.
- Interpretation: wolfSSL did return `WOLFSSL_FATAL_ERROR` for the corrupted accepted 0-RTT record, but the server's transmitted alert history remained unset instead of containing fatal `bad_record_mac`.

## Inconsistency Reason

RFC 8446 requires fatal `bad_record_mac` when a server fails to decrypt a 0-RTT record after accepting early data. wolfSSL's accepted early-data decrypt-failure path instead counts the record size, branches as ignored early data, advances the input buffer, and returns before sending `bad_record_mac`. The focused runtime test reproduced this behavior on the current build: the server reported a fatal error to the API but did not record a transmitted `bad_record_mac` alert.

## Decision Reason

Verified standard evidence, code evidence, and current runtime evidence establish a concrete divergence. The server acceptance path sets `extEarlyData->resp = 1` and `ssl->earlyData = process_early_data`; the record decrypt-failure path then returns through the early-data ignore branch before `SendAlert(ssl, alert_fatal, bad_record_mac)`. The focused unit test exercises that path with a real ticket-based 0-RTT resumption and observes `h.last_tx.code == -1` instead of `bad_record_mac` (`20`). Final verdict remains `issue_found`.
