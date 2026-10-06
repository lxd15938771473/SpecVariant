# Early-data AEAD limit incorrectly triggers KeyUpdate

## Summary

wolfSSL's TLS 1.3 early-data write path reuses the normal application-data AEAD send-limit logic. When a client reaches the AES-GCM key-usage limit during 0-RTT early data, `wolfSSL_write_early_data()` enters `SendData()`, and `CheckTLS13AEADSendLimit()` calls `Tls13UpdateKeys()`. That causes the sender to emit `KeyUpdate` before Finished and then report the caller's early-data write as successful.

This violates RFC 9846's early-data rule. Early data cannot perform KeyUpdate, so an implementation must not continue early-data sending through a KeyUpdate or updated early-data traffic key.

## Standard Requirement

Official standard links:

- [RFC 9846 Section 5.5, Limits on Key Usage](https://datatracker.ietf.org/doc/html/rfc9846#section-5.5)
- [RFC 9846 Section 4.7.3, Key and Initialization Vector Update](https://datatracker.ietf.org/doc/html/rfc9846#section-4.7.3)
- [RFC 9846 Section 7.2, Updating Traffic Secrets](https://datatracker.ietf.org/doc/html/rfc9846#section-7.2)

RFC 9846 Section 5.5 requires implementations to close the connection or perform a KeyUpdate before AEAD usage limits are reached. The same section adds an early-data-specific rule: because KeyUpdate is not possible for early data, implementations must not exceed the limits when sending early data.

Section 4.7.3 says KeyUpdate can be sent only after the sender has sent Finished. Section 7.2 likewise describes traffic-secret updates as possible once the handshake is complete.

Interpretation: ordinary TLS 1.3 application data can handle the AEAD boundary by closing or by performing KeyUpdate after Finished. 0-RTT early data does not have that option. It must stop before the limit; it cannot send `KeyUpdate`, derive update traffic keys, and continue during the early-data phase.

For AES-GCM, the relevant record limit is `2^24.5`; this wolfSSL tree uses `AEAD_AES_LIMIT = w64From32(0, 0x016A09E6)`.

## Relevant Source Code

`wolfssl/internal.h:1426`

```c
#define AEAD_AES_LIMIT                           w64From32(0, 0x016A09E6)
```

`src/tls13.c:16299`

```c
if (ssl->options.handShakeState == NULL_STATE) {
    if (ssl->error == 0)
        ssl->earlyData = expecting_early_data;
    ret = wolfSSL_connect_TLSv13(ssl);
    if (ret != WOLFSSL_SUCCESS)
        return WOLFSSL_FATAL_ERROR;
    ssl->earlyDataStatus = WOLFSSL_EARLY_DATA_REJECTED;
}
if (ssl->options.handShakeState == CLIENT_HELLO_COMPLETE) {
    ret = SendData(ssl, data, sz);
    if (ret > 0) {
        *outSz = ret;
        ssl->earlyDataSz += ret;
    }
}
```

After the client reaches `CLIENT_HELLO_COMPLETE`, `wolfSSL_write_early_data()` sends early-data plaintext through the ordinary `SendData()` record path.

`src/internal.c:27819`

```c
if (ssl->options.side == WOLFSSL_CLIENT_END &&
        ssl->earlyData != no_early_data &&
        ssl->earlyData != done_early_data) {
    if (ssl->options.handShakeState == HANDSHAKE_DONE) {
        ssl->error = BUILD_MSG_ERROR;
        return WOLFSSL_FATAL_ERROR;
    }
}
```

`SendData()` recognizes active early data, but this branch only prevents sending early data after the handshake is complete. It does not change AEAD-limit behavior while the handshake is still incomplete.

`src/internal.c:27675`

```c
seq = w64From32(ssl->keys.sequence_number_hi,
        ssl->keys.sequence_number_lo);

if (w64GTE(seq, limit)) {
    return Tls13UpdateKeys(ssl); /* Need to generate new keys */
}
```

`CheckTLS13AEADSendLimit()` calls `Tls13UpdateKeys()` directly when the send sequence reaches the cipher-specific limit. It does not check `ssl->earlyData`, so it also covers `wolfSSL_write_early_data()`.

`src/internal.c:27941`

```c
if (IsAtLeastTLSv1_3(ssl->version)) {
    ret = CheckTLS13AEADSendLimit(ssl);
    if (ret != 0) {
        ssl->error = ret;
        return WOLFSSL_FATAL_ERROR;
    }
}
```

The AEAD send-limit check runs inside `SendData()`, which is shared by early-data writes.

`src/tls13.c:12459`

```c
AddTls13Headers(output, OPAQUE8_LEN, key_update, ssl);

ret = SendBuffered(ssl);

if ((ret = DeriveTls13Keys(ssl, update_traffic_key,
               ENCRYPT_SIDE_ONLY, 1)) != 0)
    return ret;
if ((ret = SetKeysSide(ssl, ENCRYPT_SIDE_ONLY)) != 0)
    return ret;

w64Increment(&ssl->keys.keyUpdateCount);
```

`SendTls13KeyUpdate()` constructs and sends `KeyUpdate`, derives update traffic keys, installs new sending keys, and increments `keyUpdateCount`. The send path does not verify that the local endpoint has already sent Finished and does not exclude active early data.

## Implementation Behavior

Checked build configuration:

- `WOLFSSL_EARLY_DATA` is enabled.
- `NO_PSK` is defined, so the runtime probe uses session-ticket resumption instead of external PSK callbacks.
- `WOLFSSL_TLS13_IGNORE_AEAD_LIMITS` is not defined, so AEAD send-limit enforcement is active.

Observed behavior path:

1. The client begins a resumption connection that permits 0-RTT data.
2. `wolfSSL_write_early_data()` reaches `CLIENT_HELLO_COMPLETE`.
3. The call delegates early-data plaintext to `SendData()`.
4. `SendData()` invokes `CheckTLS13AEADSendLimit()`.
5. At `seq >= AEAD_AES_LIMIT`, `CheckTLS13AEADSendLimit()` calls `Tls13UpdateKeys()`.
6. `Tls13UpdateKeys()` calls `SendTls13KeyUpdate()`.
7. wolfSSL sends `KeyUpdate`, derives update traffic keys, resets the sending sequence number, increments `keyUpdateCount`, and then writes the caller's early-data record successfully.

## Inconsistency Reason

RFC 9846 separates normal post-handshake application-data key updates from 0-RTT early-data sending. Section 4.7.3 and Section 7.2 allow KeyUpdate only after Finished and after handshake completion. Section 5.5 then states the early-data-specific consequence: because KeyUpdate is not possible for early data, implementations must not exceed the key-usage limits when sending early data.

wolfSSL instead resolves the early-data AEAD boundary through the same generic KeyUpdate path used for ordinary application data. The implementation does not close or refuse the early-data send at the boundary; it sends a KeyUpdate during the early-data phase and reports the caller's write as successful.

## Runtime Evidence

Fresh rerun date: 2026-08-10.

Action: a focused probe performed a real TLS 1.3 handshake, obtained a resumption ticket with early data enabled, started a second resumption connection, stopped at `CLIENT_HELLO_COMPLETE`, and then staged the client sending sequence around `AEAD_AES_LIMIT`.

Observed output from the latest rerun:

```text
ticket_session: ticket_len=210 max_early_data=4294967295
partial_connect: ret=1 err=0 handshake_state=12 connect_state=1 early_data=2 bulk_cipher=7 server_bound_msgs=1 server_bound_len=1662
control_limit_minus_2: ret=1 err=0 written=1 before_seq=0x016a09e4 after_seq_hi=0 after_seq_lo=23726565 key_updates_before=0 key_updates_after=0 server_bound_len_delta=23 server_bound_msg_delta=1
new_server_bound_msg[0]: first_byte=0x17 size=23 tls_record_len=18
boundary_limit: ret=1 err=0 written=1 before_seq=0x016a09e6 after_seq_hi=0 after_seq_lo=1 key_updates_before=0 key_updates_after=1 server_bound_len_delta=50 server_bound_msg_delta=2
new_server_bound_msg[0]: first_byte=0x17 size=27 tls_record_len=22
new_server_bound_msg[1]: first_byte=0x17 size=23 tls_record_len=18
RESULT=CONFIRMED_KEYUPDATE_DURING_EARLY_DATA
```

Interpretation:

- The control write at `AEAD_AES_LIMIT - 2` succeeded as one early-data record and left `keyUpdateCount` unchanged.
- The boundary write at `0x016A09E6` also returned success and reported `written=1`.
- At the same boundary, `keyUpdateCount` changed from `0` to `1`, proving the sender executed `SendTls13KeyUpdate()`.
- The sender sequence number reset and then advanced to `1`, matching installation and use of update traffic keys.
- The boundary write produced two new server-bound records: one KeyUpdate-sized encrypted record and one one-byte application-data record.

## Impact

A wolfSSL client can continue writing 0-RTT early data at the AES-GCM key-usage boundary by causing the library to emit `KeyUpdate` before Finished. A compliant peer is expected to reject such a premature KeyUpdate with `unexpected_message`, and the sender also violates the Section 5.5 rule that early data must not exceed key-usage limits.

The practical trigger requires an extreme record count, but it is still a real protocol-compliance bug in builds where early data and AEAD limit enforcement are enabled. Byte-oriented `max_early_data` accounting does not replace the record-count AEAD limit, especially for many small early-data records.

## Fix Direction

Before `CheckTLS13AEADSendLimit()` calls `Tls13UpdateKeys()`, wolfSSL should detect active client early data, for example:

- client side;
- `ssl->earlyData != no_early_data`;
- `ssl->earlyData != done_early_data`;
- handshake not yet complete.

In that state, wolfSSL should refuse the early-data send at the AEAD boundary and move to an error or close path instead of sending `KeyUpdate`. The existing post-handshake application-data path can continue to use KeyUpdate at the AEAD limit.
