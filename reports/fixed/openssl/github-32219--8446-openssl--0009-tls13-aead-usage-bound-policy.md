# OpenSSL TLS 1.3 AEAD usage-bound KeyUpdate policy

This is a real behavior gap in OpenSSL's TLS record layer: OpenSSL supports TLS
1.3 KeyUpdate, but ordinary TLS writes do not automatically trigger KeyUpdate or
close the connection when the AES-GCM Section 5.5 usage bound is crossed.

The severity needs careful wording. Under RFC 8446, Section 5.5 uses `SHOULD`,
so this is a confirmed SHOULD-level compliance/security-hardening gap rather
than a MUST-level protocol violation. Under RFC 9846, which obsoletes RFC 8446,
the same behavior maps to a stronger requirement because Section 5.5 now says
implementations `MUST` either close or update keys before reaching the limits.

The post-compromise-security text from Appendix E.1.5 is not itself an OpenSSL
implementation issue. TLS explicitly does not provide PCS after a connection
traffic secret is compromised; systems that need that property must establish a
fresh connection.

## Standard Requirement

### RFC 8446 Section 5.5, Limits on Key Usage

Official standard link: <https://www.rfc-editor.org/rfc/rfc8446#section-5.5>

Relevant normative text:

```text
Implementations SHOULD do a key update as described in Section 4.6.3
prior to reaching these limits.
```

The same section gives the AES-GCM bound as up to `2^24.5` full-size records
while retaining the stated AE safety margin. This is the direct RFC 8446
requirement that should be used for this finding.

### RFC 8446 Appendix E.1.5, Post-Compromise Security

Official standard link: <https://www.rfc-editor.org/rfc/rfc8446#appendix-E.1.5>

Appendix E.1.5 states that TLS does not provide post-compromise security for
data sent after a connection traffic secret has been compromised. It then says
systems needing that guarantee need a fresh handshake and a new connection with
an (EC)DHE exchange.

This is a protocol security-property statement, not an OpenSSL requirement to
detect compromise or automatically create a new connection. It should not be
grouped into this implementation issue.

### RFC 9846 Section 5.5, Current Replacement Text

Official standard link: <https://www.rfc-editor.org/rfc/rfc9846#section-5.5>

Relevant updated normative text:

```text
Implementations MUST either close the connection or do a key update
as described in Section 4.7.3 prior to reaching these limits.
```

This report is filed under an `rfc8446-openssl` run, so the primary verdict is
based on RFC 8446. The RFC 9846 text is included to show that the same behavior
is now treated more strictly by the current TLS 1.3 replacement specification.

## Relevant Source Code

### `ssl/record/rec_layer_s3.c:305`

```c
if (s->early_data_state == SSL_EARLY_DATA_WRITING
    && !ossl_early_data_count_ok(s, len, 0, 1)) {
    /* SSLfatal() already called */
    return -1;
}
```

The write path checks configured early-data volume limits. This is separate from
the Section 5.5 AEAD usage bound for application traffic keys.

### `ssl/record/rec_layer_s3.c:313`

```c
/*
 * If we are supposed to be sending a KeyUpdate or NewSessionTicket then go
 * into init unless we have writes pending - in which case we should finish
 * doing that first.
 */
if (s->rlayer.wpend_tot == 0
    && (s->key_update != SSL_KEY_UPDATE_NONE
        || s->ext.extra_tickets_expected > 0))
    ossl_statem_set_in_init(s, 1);
```

KeyUpdate is sent when `s->key_update` has already been scheduled. This code
does not decide to schedule a KeyUpdate based on record count or protected data
volume.

### `ssl/record/rec_layer_s3.c:414`

```c
maxpipes = s->rlayer.wrlmethod->get_max_records(s->rlayer.wrl, type, n,
    max_send_fragment, &split_send_fragment);
```

Normal application writes ask the record layer how to split data into records,
then write those records. The path does not consult an AES-GCM Section 5.5
threshold before calling `write_records`.

### `ssl/record/methods/tls13_meth.c:162`

```c
offset = nonce_len - SEQ_NUM_SIZE;
memcpy(nonce, staticiv, offset);
for (loop = 0; loop < SEQ_NUM_SIZE; loop++)
    nonce[offset + loop] = staticiv[offset + loop] ^ seq[loop];

if (!tls_increment_sequence_ctr(rl)) {
    /* RLAYERfatal already called */
    return 0;
}
```

TLS 1.3 record protection forms the nonce from the static IV and current
sequence number, then increments the sequence counter.

### `ssl/record/methods/tls_common.c:2038`

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

The sequence counter rejects only 64-bit wraparound. It does not enforce the
AES-GCM `2^24.5` full-size-record usage bound.

### `ssl/ssl_lib.c:2977`

```c
int SSL_key_update(SSL *s, int updatetype)
{
    SSL_CONNECTION *sc = SSL_CONNECTION_FROM_SSL(s);

    if (!SSL_CONNECTION_IS_TLS13(sc)) {
        ERR_raise(ERR_LIB_SSL, SSL_R_WRONG_SSL_VERSION);
        return 0;
    }

    ossl_statem_set_in_init(sc, 1);
    sc->key_update = updatetype;
    return 1;
}
```

OpenSSL exposes explicit application-driven KeyUpdate support. This proves the
mechanism exists, but not that Section 5.5 usage limits are enforced
automatically.

### `ssl/tls13_enc.c:790`

```c
int tls13_update_key(SSL_CONNECTION *s, int sending)
{
    static const unsigned char application_traffic[] =
        "\x74\x72\x61\x66\x66\x69\x63\x20\x75\x70\x64";

    if (!derive_secret_key_and_iv(s, md, s->s3.tmp.new_sym_enc,
            s->s3.tmp.new_mac_pkey_type, s->s3.tmp.new_hash,
            insecret, NULL, application_traffic,
            sizeof(application_traffic) - 1, secret, key,
            &keylen, &iv, &ivlen, &taglen)) {
        goto err;
    }

    if (!ssl_set_new_record_layer(s, s->version, direction,
            OSSL_RECORD_PROTECTION_LEVEL_APPLICATION,
            insecret, hashlen, key, keylen, iv, ivlen, NULL, 0,
            s->s3.tmp.new_sym_enc, taglen, NID_undef, NULL,
            NULL, md)) {
        goto err;
    }
}
```

`tls13_update_key()` derives the next application traffic secret and installs a
new application record layer. The missing part is the automatic policy that
calls this before the Section 5.5 AES-GCM bound is reached.

## Implementation Behavior

OpenSSL's TLS implementation has:

- explicit `SSL_key_update()` API support;
- receive-side KeyUpdate handling;
- send-side state-machine support once `s->key_update` is set;
- record sequence number maintenance and 64-bit wrap rejection;
- early-data volume counting.

OpenSSL's TLS implementation does not show:

- a per-write-key record counter tied to the RFC 8446 Section 5.5 AES-GCM
  threshold;
- a TLS application-data write-path trigger that schedules KeyUpdate before the
  AES-GCM bound;
- a TLS application-data write-path fallback that closes the connection when the
  bound is reached.

The QUIC code contains separate packet-limit and `aead_limit_reached` concepts,
but that code belongs to QUIC record protection and is not used by the RFC 8446
TLS record-layer path audited here.

## Inconsistency Reason

RFC 8446 Section 5.5 recommends that implementations perform a KeyUpdate before
AEAD usage limits are reached. OpenSSL provides KeyUpdate as an explicit
application/API action, but the TLS record layer does not automatically enforce
the AES-GCM usage-bound policy during normal writes.

This is therefore a confirmed RFC 8446 SHOULD-level gap: the primitive
KeyUpdate mechanism is implemented, while the automatic usage-bound policy is
missing from the ordinary TLS write path.

The Appendix E.1.5 post-compromise-security framing should not be used as a
separate issue. TLS explicitly does not provide PCS for a connection after its
traffic secret is compromised; OpenSSL is not required by that paragraph to
detect compromise or establish a replacement connection automatically.

## Runtime Evidence

The probe performs an in-memory TLS 1.3 handshake with
`TLS_AES_128_GCM_SHA256`, sets the client write sequence to `23726565`
(`floor(2^24.5) - 1`), and then writes three 16 KiB application records. This
crosses the AES-GCM full-size-record bound without requiring a 24-million-record
test run.

Observed output:

```text
cipher=TLS_AES_128_GCM_SHA256
key_update_initial=-1
sequence_before=23726565
write_1_ret=1 ssl_err=0 written=16384 sequence=23726566 key_update=-1 state=SSL negotiation finished successfully
write_2_ret=1 ssl_err=0 written=16384 sequence=23726567 key_update=-1 state=SSL negotiation finished successfully
write_3_ret=1 ssl_err=0 written=16384 sequence=23726568 key_update=-1 state=SSL negotiation finished successfully
```

The probe completed successfully and produced no diagnostic output.

Interpretation:

- the cipher was `TLS_AES_128_GCM_SHA256`;
- writes at and beyond the AES-GCM record-count boundary succeeded;
- each write produced a full-size 16 KiB application record;
- `key_update=-1` is `SSL_KEY_UPDATE_NONE`, so no KeyUpdate was automatically
  scheduled;
- the connection remained in `SSL negotiation finished successfully`.

## Impact

For very long-lived TLS 1.3 AES-GCM connections, OpenSSL can continue using the
same write-key epoch beyond the RFC 8446 Section 5.5 recommended usage bound
unless the application proactively calls `SSL_key_update()` or closes the
connection. The security impact is bounded by the fact that RFC 8446 expresses
this as `SHOULD`, but it is still a real record-protection hardening gap for
high-volume connections.

Under RFC 9846's updated wording, this same behavior is more serious because
the current replacement specification requires close-or-KeyUpdate before the
limit.

## Fix Direction

Add TLS record-layer usage accounting for each active write-key epoch. For
AES-GCM TLS 1.3 application traffic, OpenSSL should schedule a KeyUpdate or
refuse further writes before the Section 5.5 bound is reached. The counter must
reset when `tls13_update_key()` installs a new application write record layer.

Early data should remain separate: RFC 9846 notes that KeyUpdate is not possible
for early data, so early-data sending must not exceed the applicable limits.
