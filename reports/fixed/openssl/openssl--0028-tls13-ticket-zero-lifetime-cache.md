# TLS 1.3 zero-lifetime NewSessionTicket is cached and offered


## Summary

OpenSSL's TLS 1.3 client does not discard a `NewSessionTicket` whose `ticket_lifetime` is zero. The client accepts the message, stores the opaque ticket, marks the resulting `SSL_SESSION` resumable, invokes the client-side session cache callback, and may offer that ticket as a PSK in a subsequent ClientHello while the computed ticket age is still zero.

This is a real issue relative to the RFC 8446 field semantics for `ticket_lifetime == 0`. The report should not overstate the standard text as an uppercase BCP 14 discard requirement: RFC 8446 says BCP 14 keywords apply only when uppercase, and the discard sentence uses lowercase `should`. Even with that nuance, a zero lifetime is defined as meaning immediate discard, so caching and offering the ticket is inconsistent with the field meaning.

## Standard Requirement

- Primary standard: RFC 8446, Section 4.6.1, "New Session Ticket Message"
- Related standard text: RFC 8446, Section 1.3, "Conventions and Terminology"; Section 4.2.11.1, "Ticket Age"
- Current replacement text checked: RFC 9846, Section 4.7.1 retains the same zero-lifetime discard semantics.
- Official links: [RFC 8446, Section 4.6.1](https://www.rfc-editor.org/rfc/rfc8446.html#section-4.6.1) and [RFC 9846, Section 4.7.1](https://www.rfc-editor.org/rfc/rfc9846.html#section-4.7.1)

Relevant RFC 8446 text:

```text
The value of zero indicates that the
ticket should be discarded immediately.
```

BCP 14 nuance:

```text
when, and only when, they appear in all
capitals
```

Interpretation:

The lowercase `should` means this sentence is not a BCP 14 `SHOULD`. However, it is still the protocol's definition of what a zero `ticket_lifetime` value indicates. A conforming client should treat such a ticket as having no usable lifetime and should not retain it for later PSK offering.

## Relevant Source Code

### `ssl/statem/statem_clnt.c:3103-3110`

```c
if (!PACKET_get_net_4(pkt, &ticket_lifetime_hint)
    || (SSL_CONNECTION_IS_TLS13(s)
        && (!PACKET_get_net_4(pkt, &age_add)
            || !PACKET_get_length_prefixed_1(pkt, &nonce)))
    || !PACKET_get_net_2(pkt, &ticklen)
    || (SSL_CONNECTION_IS_TLS13(s) ? (ticklen == 0
                                         || PACKET_remaining(pkt) < ticklen)
                                   : PACKET_remaining(pkt) != ticklen)) {
```

The parser validates the field order and rejects an empty TLS 1.3 ticket, but it does not reject or short-circuit when `ticket_lifetime_hint == 0`.

### `ssl/statem/statem_clnt.c:3174-3176`

```c
s->session->ext.tick_lifetime_hint = ticket_lifetime_hint;
s->session->ext.tick_age_add = age_add;
s->session->ext.ticklen = ticklen;
```

The zero lifetime is stored in the session along with the ticket bytes.

### `ssl/statem/statem_clnt.c:3220-3227`

```c
if (!EVP_Digest(s->session->ext.tick, ticklen,
        s->session->session_id, &sess_len,
        sctx->sha256, NULL)) {
    SSLfatal(s, SSL_AD_INTERNAL_ERROR, ERR_R_EVP_LIB);
    goto err;
}
s->session->session_id_length = sess_len;
s->session->not_resumable = 0;
```

The client creates a session ID from the ticket and explicitly marks the session resumable.

### `ssl/statem/statem_clnt.c:3257-3258`

```c
OPENSSL_free(exts);
ssl_update_cache(s, SSL_SESS_CACHE_CLIENT);
```

The TLS 1.3 client updates the session cache after processing the ticket.

### `ssl/statem/extensions_clnt.c:1388-1405`

```c
t = ossl_time_subtract(ossl_time_now(), s->session->time);
agesec = (uint32_t)ossl_time2seconds(t);

if (agesec > 0)
    agesec--;

if (s->session->ext.tick_lifetime_hint < agesec) {
    /* Ticket is too old. Ignore it. */
    goto dopsksess;
}
```

When a cached TLS 1.3 ticket is considered for PSK offering, OpenSSL only ignores it if `ticket_lifetime_hint < agesec`. For a zero-lifetime ticket used immediately, `agesec == 0`, so `0 < 0` is false and the PSK path remains enabled.

### `ssl/ssl_sess.c:1186-1194`

```c
int SSL_SESSION_is_resumable(const SSL_SESSION *s)
{
    /*
     * In the case of EAP-FAST, we can have a pre-shared "ticket" without a
     * session ID.
     */
    return !s->not_resumable
        && (s->session_id_length > 0 || s->ext.ticklen > 0);
}
```

The public resumability check does not treat a zero ticket lifetime as non-resumable.

## Implementation Behavior

For a received TLS 1.3 `NewSessionTicket` with `ticket_lifetime == 0` and a non-empty ticket:

1. `tls_process_new_session_ticket()` parses the message successfully.
2. The ticket bytes and `tick_lifetime_hint == 0` are stored in the session.
3. The session is marked resumable by setting a non-empty session ID and `not_resumable = 0`.
4. `ssl_update_cache(..., SSL_SESS_CACHE_CLIENT)` invokes the client-side new-session cache path.
5. A subsequent ClientHello can include the cached ticket in the `pre_shared_key` extension while `agesec == 0`.

## Inconsistency Reason

RFC 8446 defines a zero `ticket_lifetime` as indicating immediate discard. OpenSSL instead retains the ticket as a resumable TLS 1.3 session and can offer it as a PSK on the next connection. The inconsistency is specifically client-side caching and PSK offering of a ticket whose protocol-defined lifetime is zero.

The precise report wording should be:

- Confirmed: "OpenSSL caches a zero-lifetime TLS 1.3 ticket and may offer it in ClientHello."
- Not directly proven by the wire-zero probe: "A server accepts that exact constructed ticket and completes resumption."

The older mutation-based control still shows that if an otherwise valid OpenSSL ticket has its cached lifetime changed to zero, OpenSSL treats it as resumable and can resume with it. The direct zero-lifetime probe is the cleaner standards evidence because it exercises the receive-side `NewSessionTicket` parser with `ticket_lifetime == 0`.

## Runtime Evidence

### Focused direct receive-side probe

The probe builds a TLS 1.3 client/server pair, disables normal server ticket issuance, completes the bare handshake, then directly feeds the client parser a syntactically valid `NewSessionTicket` body whose first four bytes encode `ticket_lifetime == 0`. The probe captures the session through the client new-session callback and then attempts another handshake using that captured session.

The probe exited with code 0. Observed output:

```text
1..1
DIRECT_PARSE ok=1 captured=1 captured_hint=0 resumable=1 set_session=1
RESUME_ATTEMPT handshake_ok=1 client_reused=0 server_ch_psk=1 client_sh_psk=0
ok 1 - test_zero_lifetime_direct_ticket
```

Evidence interpretation:

- `DIRECT_PARSE ok=1`: `tls_process_new_session_ticket()` accepted the zero-lifetime ticket body.
- `captured=1`: the client new-session callback observed a cached session.
- `captured_hint=0`: the cached session retained `ticket_lifetime_hint == 0`.
- `resumable=1`: `SSL_SESSION_is_resumable()` treated the cached session as resumable.
- `set_session=1`: the session could be installed for a subsequent connection.
- `server_ch_psk=1`: the subsequent ClientHello contained a `pre_shared_key` extension.
- `client_reused=0` and `client_sh_psk=0`: the constructed opaque ticket was not accepted by the server for completed resumption. This does not negate the issue, because the standards mismatch is already demonstrated by caching and offering a ticket whose zero lifetime indicates immediate discard.

### Mutation-based positive control

The positive control captured a valid OpenSSL ticket, changed only its lifetime hint to zero, installed it for a new connection, and checked whether the client still offered it and whether the server resumed the session. The probe exited with code 0. Observed output:

```text
CASE name=control timeout=7200 hint=7200 resumable=1 set_session=1 initial_hint=7200 handshake_ok=1 client_reused=1 server_ch_psk=1 server_ch_psk_kex_modes=1 client_sh_psk=1
CASE name=zero_lifetime timeout=7200 hint=0 resumable=1 set_session=1 initial_hint=7200 handshake_ok=1 client_reused=1 server_ch_psk=1 server_ch_psk_kex_modes=1 client_sh_psk=1
SUMMARY positive_control_passed=1 reproducer_passed=1 reproducer_reused=1
```

This older probe mutates a valid cached OpenSSL ticket's lifetime hint to zero. It is useful as a positive control for the PSK construction and resumption path, but the direct receive-side probe above is the primary evidence for the `NewSessionTicket` parsing issue.

## Impact

The client may retain and offer a ticket that the protocol field definition says should be discarded immediately. This can cause incorrect client cache state, unnecessary PSK identities in ClientHello, and possible resumption attempts with tickets intended to have no usable lifetime. The practical security impact depends on server acceptance policy; a strict server can reject the PSK, but the client-side behavior still diverges from the zero-lifetime semantics.

## Fix Direction

In `tls_process_new_session_ticket()`, handle TLS 1.3 `ticket_lifetime_hint == 0` immediately after parsing the field and before duplicating, storing, marking resumable, or caching the session. A minimal fix direction is to consume/validate the rest of the message as needed for parser consistency, then return without installing a resumable ticket or invoking client session cache callbacks. The PSK construction path should also defensively treat `tick_lifetime_hint == 0` as not offerable.
