# TLS 1.3 ChaCha Sequence Wrap Is Not Enforced in wolfSSL

## Summary

RFC 9846 requires a TLS sender to rekey or terminate before a 64-bit record sequence number would wrap. It also requires closing the connection or performing a KeyUpdate before reaching the AEAD usage limits for the current traffic key.

After re-checking the standard, source code, and a fresh runtime rebuild/run, the confirmed default-build issue is the TLS 1.3 ChaCha20/Poly1305 send path:

- The default-enforcement ChaCha20/Poly1305 branch in `CheckTLS13AEADSendLimit` returns success without checking whether the next record would wrap the 64-bit send sequence number.
- TLS 1.3 record construction increments the two-word sequence counter and permits rollover from `2^64-1` to `0`.
- A fresh runtime probe completed a real TLS 1.3 ChaCha handshake and then successfully sent application records with the unchanged traffic key at sequence numbers `2^64-2`, `2^64-1`, and `0`.

The AES-GCM application-data path is not the main defect. In the default build, staging the AES-GCM counter at `AEAD_AES_LIMIT` caused wolfSSL to emit a KeyUpdate and then continue application data under a fresh key. RFC 9846 Section 4.7.3 requires the KeyUpdate message itself to be encrypted with the old key, so the observed old-key KeyUpdate at the boundary is expected behavior rather than an off-by-one violation.

Two related but secondary findings remain relevant:

- `wolfSSL_send_SessionTicket` sends `NewSessionTicket` through a direct encrypted handshake-message path that bypasses the application-data AEAD-limit check. One observed ticket at the AES boundary does not by itself prove the AES safety limit is exceeded, but the path is not covered by the central send-limit gate.
- Builds that explicitly define `WOLFSSL_TLS13_IGNORE_AEAD_LIMITS` continue encrypting AES-GCM application records past the configured limit under the same key. This is an unsafe/noncompliant build policy, not evidence that the default AES-GCM application-data path is broken.

## Standard Requirement

Official standard links:

- [RFC 9846 Section 5.3, Per-Record Nonce](https://datatracker.ietf.org/doc/html/rfc9846#section-5.3)
- [RFC 9846 Section 5.5, Limits on Key Usage](https://datatracker.ietf.org/doc/html/rfc9846#section-5.5)
- [RFC 9846 Section 4.7.3, Key and Initialization Vector Update](https://datatracker.ietf.org/doc/html/rfc9846#section-4.7.3)
- [RFC 9846 Section 4.7.1, New Session Ticket Message](https://datatracker.ietf.org/doc/html/rfc9846#section-4.7.1)

RFC 9846 Section 5.3, "Per-Record Nonce", requires per-key sequence numbers to start at zero, increment per record, and not wrap:

```text
A 64-bit sequence number is maintained separately for reading and writing records.
The appropriate sequence number is incremented by one after reading or writing
each record. Each sequence number is set to zero at the beginning of a connection
and whenever the key is changed; the first record transmitted under a particular
traffic key MUST use sequence number 0.

Because the size of sequence numbers is 64-bit, they should not wrap. If a TLS
implementation would need to wrap a sequence number, it MUST either rekey
(Section 4.7.3) or terminate the connection.
```

RFC 9846 Section 5.5, "Limits on Key Usage", requires senders to close or update keys before AEAD limits are reached:

```text
Implementations MUST either close the connection or do a key update as described
in Section 4.7.3 prior to reaching these limits.
```

The same section states that ChaCha20/Poly1305 reaches sequence-number exhaustion before its cryptographic safety limit:

```text
For AES-GCM, up to 2^24.5 full-size records (about 24 million) may be encrypted
under a given set of keys while keeping a safety margin of approximately 2^-57
for Authenticated Encryption (AE) security. For ChaCha20/Poly1305, the record
sequence number would wrap before the safety limit is reached.
```

This ChaCha text is not an exception to Section 5.3. It identifies the sequence-wrap boundary as the first applicable limit for ChaCha20/Poly1305.

RFC 9846 Section 4.7.3, "Key and Initialization Vector Update", also requires KeyUpdate itself to use the old keys:

```text
Both sender and receiver MUST encrypt their KeyUpdate messages with the old keys.
```

That requirement is important when interpreting AES-GCM runtime evidence: an old-key KeyUpdate at the AES boundary is compatible with the standard when the following application traffic moves to the next key generation.

RFC 9846 Section 4.7.1, "New Session Ticket Message", permits multiple tickets on one connection:

```text
Servers MAY send multiple tickets on a single connection, either immediately
after each other or after specific events.
```

Because `NewSessionTicket` is encrypted post-handshake traffic, those sends still consume the record sequence number and AEAD usage budget.

## Relevant Source Code

### `implementions/wolfssl-master/src/internal.c:106`

```c
 * WOLFSSL_TLS13_IGNORE_AEAD_LIMITS:
 *                  Ignore AEAD message limits from RFC 8446           default: off
```

The ignore macro is documented as default-off.

### `implementions/wolfssl-master/src/internal.c:27609`

```c
#if !defined(NO_TLS) && defined(WOLFSSL_TLS13) && \
    !defined(WOLFSSL_TLS13_IGNORE_AEAD_LIMITS)
static int CheckTLS13AEADSendLimit(WOLFSSL* ssl)
```

The send-limit helper is compiled only when TLS 1.3 support is enabled and the ignore macro is not defined.

### `implementions/wolfssl-master/src/internal.c:27619`

```c
switch (ssl->specs.bulk_cipher_algorithm) {
#ifdef BUILD_AESGCM
    case wolfssl_aes_gcm:
        /* Limit is 2^24.5 */
        limit = AEAD_AES_LIMIT;
        break;
#endif
#if defined(HAVE_CHACHA) && defined(HAVE_POLY1305)
    case wolfssl_chacha:
        /* For ChaCha20/Poly1305, the record sequence number would wrap
         * before the safety limit is reached. */
        return 0;
#endif
```

AES-GCM reaches a concrete send limit. ChaCha20/Poly1305 returns success before the code reads the current TLS sequence number, so it cannot detect imminent wrap.

### `implementions/wolfssl-master/src/internal.c:27679`

```c
if (w64GTE(seq, limit)) {
    return Tls13UpdateKeys(ssl); /* Need to generate new keys */
}
```

For ciphers that reach this code, wolfSSL updates keys when the current send sequence is greater than or equal to the configured limit.

### `implementions/wolfssl-master/src/internal.c:27941`

```c
#if defined(WOLFSSL_TLS13) && !defined(WOLFSSL_TLS13_IGNORE_AEAD_LIMITS)
    if (IsAtLeastTLSv1_3(ssl->version)) {
        ret = CheckTLS13AEADSendLimit(ssl);
```

The helper is called from the TLS 1.3 `SendData` application-data path.

### `implementions/wolfssl-master/src/tls13.c:2456`

```c
static WC_INLINE void WriteSEQTls13(WOLFSSL* ssl, int verifyOrder, byte* out)
{
    ...
    else {
        seq[0] = ssl->keys.sequence_number_hi;
        seq[1] = ssl->keys.sequence_number_lo++;
        /* handle rollover */
        if (seq[1] > ssl->keys.sequence_number_lo)
            ssl->keys.sequence_number_hi++;
    }
```

`WriteSEQTls13` post-increments the two-word TLS 1.3 sequence counter. When both words are all ones, the stored counter rolls over to `0:0`.

### `implementions/wolfssl-master/src/tls13.c:2503`

```c
WriteSEQTls13(ssl, order, nonce + seq_offset);
```

The sequence number is incorporated into the per-record nonce during TLS 1.3 record construction.

### `implementions/wolfssl-master/src/keys.c:3405`

```c
if (enc) {
    keys->sequence_number_hi      = 0;
    keys->sequence_number_lo      = 0;
}
```

Successful key installation resets the sender sequence number to zero, matching RFC 9846's per-key sequence rule.

### `implementions/wolfssl-master/src/tls13.c:13037`

```c
static int SendTls13NewSessionTicket(WOLFSSL* ssl)
```

### `implementions/wolfssl-master/src/tls13.c:13204`

```c
/* This message is always encrypted. */
sendSz = BuildTls13Message(ssl, output, sendSz,
                           output + RECORD_HEADER_SZ,
                           (word16)idx - RECORD_HEADER_SZ,
                           handshake, 0, 0, 0);
```

`SendTls13NewSessionTicket` builds an encrypted TLS 1.3 handshake record directly. This function does not call `CheckTLS13AEADSendLimit`.

### `implementions/wolfssl-master/src/tls13.c:16151`

```c
int wolfSSL_send_SessionTicket(WOLFSSL* ssl)
{
    ...
    if ((ssl->error = SendTls13NewSessionTicket(ssl)) != 0) {
```

The public `wolfSSL_send_SessionTicket` API delegates directly to `SendTls13NewSessionTicket`.

## Implementation Behavior

The default AES-GCM application-data path behaves as a positive control. The implementation checks `AEAD_AES_LIMIT` before sending application data. When the staged send counter equals `23726566`, wolfSSL first emits a KeyUpdate using the old key, installs the next sending key generation, resets the send sequence to zero, and then sends application data under the updated key.

The confirmed default-build defect is the ChaCha20/Poly1305 branch. The implementation comment correctly notes that sequence wrap occurs before the ChaCha cryptographic safety limit, but the branch treats that fact as a reason to skip all enforcement. Because `WriteSEQTls13` permits rollover, the sender can construct a record using sequence `0` under the unchanged traffic key after it has already used sequence `2^64-1`.

The session-ticket path is a coverage gap in session-ticket builds. `NewSessionTicket` is encrypted and consumes the record sequence/AEAD budget, but it is sent through `BuildTls13Message` and `SendBuffered` rather than the application-data `SendData` path where `CheckTLS13AEADSendLimit` is called. The runtime probe confirms one such ticket can be emitted at the AES boundary under the baseline key. A single ticket at that point is not enough to prove the AES limit has been exceeded, but repeated ticket sends can consume budget outside the normal guard.

## Inconsistency Reason

RFC 9846 requires a sender that would need to wrap a 64-bit sequence number to rekey or terminate. wolfSSL's default ChaCha20/Poly1305 path returns from `CheckTLS13AEADSendLimit` without checking the current sequence number, and the TLS 1.3 nonce construction then allows the sequence counter to wrap. The fresh runtime run confirms a record with sequence `0` is sent under the unchanged traffic key immediately after sequence `2^64-1`.

RFC 9846 also requires closing or key update before AEAD usage limits are reached. wolfSSL enforces this for the normal default AES-GCM application-data path, but the enforcement is not uniformly applied to encrypted non-application-data send paths such as `NewSessionTicket`. That second finding is best described as an AEAD-limit coverage gap, while the ChaCha sequence-wrap behavior is the direct confirmed violation.

## Runtime Evidence

The issue was rechecked on 2026-08-10 with fresh default and ignore-policy builds, plus freshly compiled probes.

Action: the default build was configured and built, the ignore-policy build with `WOLFSSL_TLS13_IGNORE_AEAD_LIMITS` was configured and built, and both probes were compiled. All configure, build, and compile steps exited with code `0`. Runtime stderr was empty for all focused cases; CMake configure stderr contained only platform diagnostics.

Observed case results:

| Case | Observed result |
|---|---|
| Default AES-GCM, start at `limit-1` | `result=PASS default_policy_keyupdate_observed` |
| Default AES-GCM, start at `limit` | `result=PASS default_policy_keyupdate_observed` |
| Ignore-policy AES-GCM, start at `limit-1` | `result=PASS ignored_policy_continues_same_key_past_limit` |
| Ignore-policy AES-GCM, start at `limit` | `result=PASS ignored_policy_continues_same_key_past_limit` |
| Default ChaCha20/Poly1305, start at `2^64-2` | `result=PASS chacha_same_key_sequence_wrap_observed` |
| Default AES-GCM NewSessionTicket, start at `limit` | `result=PASS session_ticket_used_baseline_key_at_limit` |

Action: the default ChaCha probe performed a real TLS 1.3 handshake using `TLS_CHACHA20_POLY1305_SHA256`, staged the send sequence near `2^64-1`, and wrote three application records under the same traffic key.

Representative default ChaCha log excerpt:

```text
positive_control=handshake_ok version=TLSv1.3 cipher=TLS_CHACHA20_POLY1305_SHA256 bulk=chacha
record index=1 send_call=1 tls_outer_type=23 ciphertext_len=19 inferred_used_seq=18446744073709551614 key_generation=baseline counter_after_build=4294967295:4294967295
record index=2 send_call=2 tls_outer_type=23 ciphertext_len=19 inferred_used_seq=18446744073709551615 key_generation=baseline counter_after_build=0:0
record index=3 send_call=3 tls_outer_type=23 ciphertext_len=19 inferred_used_seq=0 key_generation=baseline counter_after_build=0:1
result=PASS chacha_same_key_sequence_wrap_observed
```

Observation: wolfSSL sent records using sequence `2^64-2`, then `2^64-1`, then sequence `0` under the baseline key generation. This confirms sequence-number wrap under an unchanged ChaCha20/Poly1305 traffic key.

Action: the default AES-GCM control performed a real TLS 1.3 handshake using `TLS_AES_128_GCM_SHA256`, staged the send counter at the AES boundary, and wrote application data.

Representative default AES-GCM control excerpt:

```text
positive_control=handshake_ok version=TLSv1.3 cipher=TLS_AES_128_GCM_SHA256 bulk=aes_gcm
write_begin index=1 client_counter=0:23726566 key_generation=baseline
record index=1 send_call=1 tls_outer_type=23 ciphertext_len=22 inferred_used_seq=23726566 key_generation=baseline counter_after_build=0:23726567
record index=2 send_call=2 tls_outer_type=23 ciphertext_len=19 inferred_used_seq=0 key_generation=updated counter_after_build=0:1
result=PASS default_policy_keyupdate_observed
```

Observation: the AES-GCM control emitted a KeyUpdate at the boundary and then sent application data under the updated key generation. This confirms the normal AES-GCM application-data path is not the main defect.

Action: the session-ticket coverage probe staged the AES-GCM server counter at the boundary and invoked the TLS 1.3 session-ticket send path.

Representative session-ticket coverage-gap excerpt:

```text
positive_control=handshake_ok version=TLSv1.3 cipher=TLS_AES_128_GCM_SHA256 bulk=aes_gcm
ticket_begin server_counter=0:23726566 key_generation=baseline
record index=1 send_call=1 tls_outer_type=23 ciphertext_len=209 inferred_used_seq=23726566 key_generation=baseline counter_after_build=0:23726567
ticket_end ret=1 server_error=0 server_counter=0:23726567 key_generation=baseline queued_bytes=214
result=PASS session_ticket_used_baseline_key_at_limit
```

Observation: a `NewSessionTicket` record was emitted under the baseline key at the AES boundary. One ticket at that point does not prove the AES limit was exceeded by itself, but it shows the ticket path consumes encrypted-record budget outside the normal application-data guard.

Test scope note: the probes perform real TLS 1.3 handshakes and real record writes over in-memory I/O, then white-box stage the record counters near the real boundary. This proves the boundary behavior is reachable in the implementation without sending millions or quintillions of records. It does not measure long-duration production throughput.

## Impact

For TLS 1.3 ChaCha20/Poly1305 connections, an extremely long-lived sender can violate the protocol-level sequence-wrap requirement and reuse sequence number `0` under an unchanged traffic key. This is a direct RFC compliance issue even though the ChaCha cryptographic safety limit would otherwise be higher than the 64-bit sequence space.

For TLS 1.3 session-ticket builds, encrypted `NewSessionTicket` records can consume key-usage budget outside the normal application-data guard. Repeated sends near the boundary can exceed the intended AEAD usage budget without a KeyUpdate or close decision.

For builds that explicitly define `WOLFSSL_TLS13_IGNORE_AEAD_LIMITS`, AES-GCM records can continue past the configured limit under the same key. Because that option is documented as default-off, it should be reported as an unsafe/noncompliant build policy rather than the main default-build defect.

## Fix Direction

- In the ChaCha20/Poly1305 TLS 1.3 send path, check for imminent 64-bit sequence wrap before constructing the record and either call `Tls13UpdateKeys` or terminate the connection.
- Apply TLS 1.3 AEAD usage-limit enforcement to encrypted non-application-data send paths that call `BuildTls13Message` directly, including `NewSessionTicket`.
- Keep `WOLFSSL_TLS13_IGNORE_AEAD_LIMITS` default-off and document or gate it as a noncompliant testing/diagnostic policy.
- Preserve the current default AES-GCM application-data behavior that emits KeyUpdate at the boundary and resumes application data under a fresh traffic key.
