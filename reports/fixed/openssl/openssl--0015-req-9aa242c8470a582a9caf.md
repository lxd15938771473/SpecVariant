# OpenSSL TLS 1.3 early-data AES-GCM key-usage limit is not enforced

## Summary

- Risk: configuration-dependent; exposed when a large early-data allowance is combined with many small early-data records
- Path policy: all file paths in this report are relative paths; source-code paths are relative to `implementions/openssl-master`

OpenSSL enforces the negotiated `max_early_data` byte budget when sending TLS
1.3 early data, but it does not enforce the RFC 9846 Section 5.5 AES-GCM
per-key usage limit as a record/key-usage limit. Because early data cannot
perform `KeyUpdate`, a sender must refuse before that cryptographic limit is
exceeded. The current TLS record path only reaches a hard failure when the
64-bit record sequence counter wraps.

## Standard Requirement

Official standard: RFC 9846, Section 5.5, "Limits on Key Usage": <https://www.rfc-editor.org/rfc/rfc9846.html#section-5.5>.

Relevant standard text:

- RFC 9846 Section 5.5, "Limits on Key Usage": <https://www.rfc-editor.org/rfc/rfc9846.html#section-5.5>
- RFC 9846 Section 4.7.3, "Key and Initialization Vector Update": <https://www.rfc-editor.org/rfc/rfc9846.html#section-4.7.3>
- RFC 9846 Section 4.7.1, "New Session Ticket Message": <https://www.rfc-editor.org/rfc/rfc9846.html#section-4.7.1>

Normative text:

```text
Implementations MUST either close the connection or do a key update
as described in Section 4.7.3 prior to reaching these limits.  Note
that it is not possible to perform a KeyUpdate for early data;
therefore, implementations MUST NOT exceed the limits when sending
early data.
```

AES-GCM limit text:

```text
For AES-GCM, up to 2^24.5 full-size records (about 24 million) may be
encrypted under a given set of keys while keeping a safety margin of
approximately 2^-57 for Authenticated Encryption (AE) security.
```

The `max_early_data_size` field is a separate TLS early-data allowance:

```text
max_early_data_size:  The maximum amount of 0-RTT data that the
client is allowed to send when using this ticket, in bytes.  Only
Application Data payload (i.e., plaintext but not padding or the
inner content type byte) is counted.
```

Interpretation:

RFC 9846 requires sending implementations to avoid exceeding the applicable
AEAD key-usage limits. For ordinary application data, the sender can close or
perform `KeyUpdate`; for early data, `KeyUpdate` is explicitly unavailable, so
the sender must stop before the limit is exceeded. The `max_early_data_size`
byte allowance does not itself prove compliance with Section 5.5, because it
does not track AES-GCM records, sequence usage, or a full-size-record boundary.

## Relevant Source Code

### Early-data byte accounting

`ssl/record/rec_layer_s3.c:150-171`

```c
if (s->early_data_count + length > max_early_data) {
    SSLfatal(s, send ? SSL_AD_INTERNAL_ERROR : SSL_AD_UNEXPECTED_MESSAGE,
        SSL_R_TOO_MUCH_EARLY_DATA);
    return 0;
}
s->early_data_count += (uint32_t)length;
```

`ssl/record/rec_layer_s3.c:305-306`

```c
if (s->early_data_state == SSL_EARLY_DATA_WRITING
    && !ossl_early_data_count_ok(s, len, 0, 1)) {
```

This path checks application byte length against `max_early_data`. It does not
count protected TLS records and does not compare against the AES-GCM key-usage
limit from RFC 9846 Section 5.5.

### TLS 1.3 record encryption sequence handling

`ssl/record/methods/tls13_meth.c:156-170`

```c
/* Set up nonce: part of static IV followed by sequence number */
if (nonce_len < SEQ_NUM_SIZE) {
    /* Should not happen */
    RLAYERfatal(rl, SSL_AD_INTERNAL_ERROR, ERR_R_INTERNAL_ERROR);
    return 0;
}
offset = nonce_len - SEQ_NUM_SIZE;
memcpy(nonce, staticiv, offset);
for (loop = 0; loop < SEQ_NUM_SIZE; loop++)
    nonce[offset + loop] = staticiv[offset + loop] ^ seq[loop];

if (!tls_increment_sequence_ctr(rl)) {
```

`ssl/record/methods/tls_common.c:2038-2053`

```c
int tls_increment_sequence_ctr(OSSL_RECORD_LAYER *rl)
{
    int i;

    /* Increment the sequence counter */
    for (i = SEQ_NUM_SIZE; i > 0; i--) {
        ++(rl->sequence[i - 1]);
        if (rl->sequence[i - 1] != 0)
            break;
    }
    if (i == 0) {
        /* Sequence has wrapped */
        RLAYERfatal(rl, SSL_AD_INTERNAL_ERROR, SSL_R_SEQUENCE_CTR_WRAPPED);
        return 0;
    }
    return 1;
}
```

The TLS 1.3 record layer increments one sequence value per protected record and
rejects only on 64-bit wrap. No AES-GCM `2^24.5` key-usage threshold is checked
here.

### Public early-data allowance

`ssl/ssl_lib.c:7386-7405`

```c
int SSL_CTX_set_max_early_data(SSL_CTX *ctx, uint32_t max_early_data)
{
    ctx->max_early_data = max_early_data;

    return 1;
}

int SSL_set_max_early_data(SSL *s, uint32_t max_early_data)
{
    SSL_CONNECTION *sc = SSL_CONNECTION_FROM_SSL_ONLY(s);
```

The public setting is a byte allowance. It can be configured to values that
permit many very small early-data records. That byte check is different from
an AES-GCM key-usage guard.

## Implementation Behavior

OpenSSL correctly implements the early-data byte allowance: a client cannot
send more 0-RTT Application Data payload bytes than the session/configuration
permits. That implemented check is separate from the cryptographic key-usage
limit in RFC 9846 Section 5.5.

For AES-GCM early data, OpenSSL has no sender-side guard that refuses before
the record/key-usage limit is reached. A large `max_early_data` value can allow
many small early-data records, and the TLS record method itself also accepts a
manually staged full-size early-data record after the Section 5.5 full-size
record boundary has already been crossed.

## Inconsistency Reason

RFC 9846 requires sending implementations to close or update keys before
reaching AEAD key-usage limits; it then states that `KeyUpdate` is impossible
for early data, so early-data senders must not exceed the limits. OpenSSL
enforces only `max_early_data` bytes and generic 64-bit sequence wrap. Those
checks are not equivalent to an AES-GCM key-usage guard.

This issue should not be described as "OpenSSL has no early-data limit".
OpenSSL does have a byte limit. The precise issue is:

OpenSSL's TLS 1.3 early-data sender enforces `max_early_data` bytes but does
not enforce the RFC 9846 AES-GCM per-key usage limit as a record/key-usage
limit, so a large early-data allowance combined with fragmented records can
cross the required sending limit without `KeyUpdate` being possible.

## Runtime Evidence

### 0-RTT CLI rerun

Purpose: confirm that a real TLS 1.3 0-RTT path can negotiate a large early-data
allowance and emit an early-data Application Data record under an AES-GCM
ciphersuite.

What was done: a scripted OpenSSL client/server 0-RTT rerun was performed. The
first connection obtained a resumable TLS 1.3 session, and the second
connection attempted early data under an AES-GCM TLS 1.3 ciphersuite with a
large `Max Early Data` allowance.

Run result:

```text
sess_size=1433
record_lines=31
```

Session evidence:

```text
Protocol  : TLSv1.3
Cipher    : TLS_AES_256_GCM_SHA384
Max Early Data: 2147483647
```

Server receive evidence:

```text
A
```

Observed client early-data record trace:

```text
>>> TLS 1.2, RecordHeader [length 0005]
    17 03 03 00 12
>>> TLS 1.2, InnerContent [length 0001]
    17
```

The `0x0012` encrypted record length is consistent with one byte of payload,
one TLS 1.3 inner content type byte, and a 16-byte GCM tag. This confirms that
OpenSSL can emit a one-byte early-data Application Data record under
`TLS_AES_256_GCM_SHA384` with a very large early-data allowance.

### Full-size early-data record-layer rerun

Purpose: remove ambiguity about small-record accounting by testing a full-size
TLS early-data record after the AES-GCM full-size-record boundary has already
been crossed.

What was done: a focused record-layer probe was run to remove ambiguity about
small-record accounting. The probe staged an early-data AES-256-GCM write
sequence beyond the RFC 9846 AES-GCM full-size-record boundary and then tried
to write one full-size TLS plaintext record.

Probe setup:

- TLS 1.3 record method
- `OSSL_RECORD_PROTECTION_LEVEL_EARLY`
- AES-256-GCM
- `payload_len=16384`, i.e. `SSL3_RT_MAX_PLAIN_LENGTH`
- `start_sequence=23726567`, which is above `floor(2^24.5)=23726566`

Rerun output:

```text
cipher=TLS_AES_256_GCM_SHA384 equivalent record method
protection_level=early_data
payload_len=16384
floor_2^24.5=23726566
start_sequence=23726567
post_write_sequence=23726568
write_records=success
wire_bytes_pending=16406
record_header=17 03 03 40 11
```

The `0x4011` encrypted record length is consistent with a 16384-byte payload,
one TLS 1.3 inner content type byte, and a 16-byte GCM tag. The record-layer
write succeeds even though the early-data sequence was staged past the RFC 9846
AES-GCM full-size-record bound.

### Established TLS 1.3 AES-GCM control rerun

Purpose: confirm the same OpenSSL build does not automatically close or send
`KeyUpdate` when the AES-GCM write sequence is staged beyond the Section 5.5
boundary on an established TLS 1.3 connection.

What was done: an established TLS 1.3 AES-GCM control probe was run against the
same OpenSSL build. The probe staged the AES-GCM write sequence past the
Section 5.5 boundary on an already established TLS 1.3 connection and then
attempted a full-size application write.

Rerun output:

```text
negotiated=TLSv1.3 cipher=TLS_AES_128_GCM_SHA256
rfc9846_aes_gcm_full_size_record_limit_floor=23726566
forced_write_sequence_before=23726567
ssl_write_ex_ret=1 err=0 written=16384
write_sequence_after=23726568
keyupdate_write_requested=0 keyupdate_write_not_requested=0
shutdown_flags=0 key_update_pending=-1
```

This control is not the early-data finding by itself, but it confirms the same
record/write stack has no automatic AES-GCM key-usage enforcement or automatic
`KeyUpdate` at the tested boundary.

## Impact

The impact is narrow and configuration-dependent. Typical deployments with
small early-data allowances or coarse full-size writes are less likely to reach
the AES-GCM record/key-usage boundary. The problematic case is a large 0-RTT
allowance and an application pattern that emits many fragmented early-data
records.

Because early data cannot perform `KeyUpdate`, the sender has no
standards-compliant way to continue once the cryptographic limit is reached or
about to be reached. It should stop sending early data before crossing the
AES-GCM limit.

## Fix Direction

Add sender-side key-usage accounting for TLS 1.3 AES-GCM early-data traffic
keys. The guard should reject or end early-data sending before the AES-GCM
record/key-usage limit is reached, rather than relying only on
`max_early_data` bytes or 64-bit sequence wrap.

The fix should preserve the existing `max_early_data` byte check, because that
check implements a different TLS requirement. The new check should be
cipher-aware; for non-AES-GCM ciphersuites, apply the relevant RFC 9846
key-usage rule.
