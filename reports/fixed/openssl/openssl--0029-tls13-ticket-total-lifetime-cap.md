# TLS 1.3 tickets can be refreshed without a cumulative lifetime cap

## Summary

OpenSSL's default TLS 1.3 ticket path enforces a finite lifetime for each individual ticket, but it does not enforce a separate cumulative lifetime for the resumption chain that originates from the first non-PSK handshake. When a resumed handshake succeeds, the server can issue a replacement NewSessionTicket, and the client stores that replacement as a new session with a fresh start time.

This is a real implementation-policy gap against the TLS 1.3 standard's recommendation, but it is not a `MUST`-level protocol violation. The current TLS 1.3 standard is RFC 9846, which obsoletes RFC 8446. The relevant requirement remains present with the same recommendation strength: RFC 8446 Section 4.6.1 became RFC 9846 Section 4.7.1.


## Standard Requirement

- Current standard: [RFC 9846 Section 4.7.1, "New Session Ticket Message"](https://www.rfc-editor.org/rfc/rfc9846.html#section-4.7.1)
- Obsoleted source with same wording: [RFC 8446 Section 4.6.1, "New Session Ticket Message"](https://www.rfc-editor.org/rfc/rfc8446.html#section-4.6.1)

```text
It is RECOMMENDED that
   implementations place limits on the total lifetime of such keying
   material
```

The surrounding paragraph says repeated ticket issuance can indefinitely extend keying material originally derived from an initial non-PSK handshake, and that total-lifetime limits should account for peer certificate lifetime, possible revocation, and elapsed time since the online `CertificateVerify` signature.

RFC 9846 Section 1.1 applies BCP 14 terminology and includes `RECOMMENDED` among the normative keywords. Therefore the requirement has SHOULD-level force. It is stronger than a casual note, but weaker than a hard `MUST`.

RFC 9846 also updates the PSK derivation notation to `HKDF-Expand-Label(resumption_secret, "resumption", ticket_nonce, Hash.length)`. The OpenSSL source still uses the implementation field name `resumption_master_secret`, which matches the older RFC 8446 terminology.

## Relevant Source Code

### Ticket issuance after resumption

`ssl/statem/statem_srvr.c:720-728` sends the TLS 1.3 server state machine to `TLS_ST_SW_SESSION_TICKET` whenever ticket issuance is enabled and the configured ticket count has not been exhausted:

```c
if (s->num_tickets <= s->sent_tickets
    || ((s->options & SSL_OP_NO_TICKET) != 0
        && (SSL_CONNECTION_GET_CTX(s)->session_cache_mode & SSL_SESS_CACHE_SERVER)
            == 0)
    || s->ext.psk_kex_mode == TLSEXT_KEX_MODE_FLAG_NONE
    || ((s->verify_mode & SSL_VERIFY_PEER) != 0 && s->sid_ctx_length == 0))
    st->hand_state = TLS_ST_OK;
else
    st->hand_state = TLS_ST_SW_SESSION_TICKET;
```

`ssl/statem/statem_srvr.c:737-745` limits a resumed handshake to one new ticket, but it still allows that replacement ticket:

```c
/* In a resumption we only ever send a maximum of one new ticket.
 * Following an initial handshake we send the number of tickets we have
 * been configured for.
 */
if (!SSL_IS_FIRST_HANDSHAKE(s) && s->ext.extra_tickets_expected > 0) {
    return WRITE_TRAN_CONTINUE;
} else if (s->hit || s->num_tickets <= s->sent_tickets) {
    st->hand_state = TLS_ST_OK;
}
```

### Individual ticket lifetime

`ssl/statem/statem_srvr.c:4238-4256` caps the advertised TLS 1.3 ticket lifetime to one week:

```c
uint32_t timeout = (uint32_t)ossl_time2seconds(s->session->timeout);

#define ONE_WEEK_SEC (7 * 24 * 60 * 60)

if (SSL_CONNECTION_IS_TLS13(s)) {
    if (ossl_time_compare(s->session->timeout,
            ossl_seconds2time(ONE_WEEK_SEC))
        > 0)
        timeout = ONE_WEEK_SEC;
}
```

`ssl/t1_lib.c:98-104` sets the built-in default session timeout to two hours:

```c
OSSL_TIME tls1_default_timeout(void)
{
    return ossl_seconds2time(60 * 60 * 2);
}
```

These checks bound a single ticket's lifetime. They do not define a total lifetime budget for a chain of refreshed tickets.

### Replacement ticket construction

When constructing a TLS 1.3 NewSessionTicket, `ssl/statem/statem_srvr.c:4544-4587` duplicates the resumed session if needed, derives a new PSK from the current `resumption_master_secret` and ticket nonce, and then resets the session timestamp:

```c
if (s->sent_tickets != 0 || s->hit) {
    SSL_SESSION *new_sess = ssl_session_dup(s->session, 0);
    ...
    s->session = new_sess;
}

...
if (!tls13_hkdf_expand(s, md, s->resumption_master_secret,
        nonce_label, sizeof(nonce_label), tick_nonce,
        TICKET_NONCE_SIZE, s->session->master_key,
        hashlen, 1)) {
    goto err;
}

s->session->time = ossl_time_now();
ssl_session_calculate_timeout(s->session);
```

`ssl/tls13_enc.c:695-704` creates a fresh `resumption_master_secret` for each completed handshake transcript, including resumed handshakes:

```c
if (label == client_application_traffic) {
    if (!tls13_hkdf_expand(s, ssl_handshake_md(s), insecret,
            resumption_master_secret,
            sizeof(resumption_master_secret) - 1,
            hashval, hashlen, s->resumption_master_secret,
            hashlen, 1)) {
        goto err;
    }
}
```

This refreshes the immediate handshake's resumption secret, but the default path does not record or enforce the origin time of the certificate-authenticated, non-PSK handshake from which the resumption chain began.

### Client ticket storage

`ssl/statem/statem_clnt.c:3157-3175` resets the received ticket session time and stores the ticket lifetime hint:

```c
s->session->time = ossl_time_now();
ssl_session_calculate_timeout(s->session);

...
s->session->ext.tick_lifetime_hint = ticket_lifetime_hint;
s->session->ext.tick_age_add = age_add;
s->session->ext.ticklen = ticklen;
```

`ssl/statem/statem_clnt.c:3181-3187` caps received TLS 1.3 ticket lifetimes above seven days, and `ssl/statem/extensions_clnt.c:1388-1404` checks whether the individual ticket age exceeds the individual ticket lifetime before offering it. Neither path enforces a cumulative origin lifetime across refreshed tickets.

## Implementation Behavior

With default TLS 1.3 tickets enabled, OpenSSL can follow this sequence:

1. Complete a full certificate-authenticated handshake.
2. Send a NewSessionTicket with a finite per-ticket lifetime.
3. Resume with that ticket.
4. Send another NewSessionTicket after the resumed handshake.
5. Store that replacement as a new resumable session whose timer starts at receipt.

Repeating steps 3-5 can keep the resumption chain alive beyond the lifetime of any individual ticket. The default per-hop timeout is two hours, and the wire lifetime is capped at seven days, but there is no built-in "originating full-handshake time plus cumulative maximum" check.

## Inconsistency Reason

RFC 9846 recommends limiting the total lifetime of keying material rooted in an initial non-PSK handshake. OpenSSL implements individual ticket lifetime limits and exposes deployment controls that can reduce or disable resumption, but the default implementation path does not maintain a lineage timestamp or cumulative lifetime budget.

The inconsistency is therefore confirmed as a SHOULD-level policy gap: OpenSSL has the per-ticket controls, but it does not implement the recommended total-lifetime cap by default.

## Runtime Evidence

The focused check used OpenSSL `4.1.0-dev`. It started a TLS 1.3 server with `-num_tickets 1`, performed one full handshake followed by two chained resumptions, and saved the session returned by each connection. It then inspected each session's status, lifetime hint, start time, timeout, and SHA-256 digest.

Observed output:

```text
client0_status=New, TLSv1.3, Cipher is TLS_AES_256_GCM_SHA384
    TLS session ticket lifetime hint: 7200 (seconds)
    Start Time: 1785931433
    Timeout   : 7200 (sec)
session0_sha256=f9c3cf5fefc104f9c21b90c011e3c4dce740d6e1843e7786d46003617efb0a20
client1_status=Reused, TLSv1.3, Cipher is TLS_AES_256_GCM_SHA384
    TLS session ticket lifetime hint: 7200 (seconds)
    Start Time: 1785931435
    Timeout   : 7200 (sec)
session1_sha256=854ce37b4ea3ae7c732075c5f22347d05c5012bee4c3f5bc1987c24b7298c773
client2_status=Reused, TLSv1.3, Cipher is TLS_AES_256_GCM_SHA384
    TLS session ticket lifetime hint: 7200 (seconds)
    Start Time: 1785931438
    Timeout   : 7200 (sec)
session2_sha256=2add5953927b9e8d95678f903bc04830f35406e94f24725b84023d5ebf46f137
```

The distinct SHA-256 hashes confirm that each resumption produced a distinct replacement ticket/session. The later sessions were both `Reused` handshakes, and their `Start Time` values advanced while their timeout remained 7200 seconds. That is runtime evidence that the per-ticket lifetime is refreshed across a resumption chain.

## Impact

If a deployment leaves TLS 1.3 ticket renewal enabled indefinitely, clients can continue resuming without a fresh certificate-authenticated handshake as long as each hop occurs before the current ticket expires. That can weaken the operational value of certificate expiry, revocation, or policy changes that would otherwise be observed during a new full handshake.

The default two-hour per-ticket lifetime limits any single ticket, but it does not cap the chain. The practical impact is configuration-sensitive because applications can set shorter session timeouts, disable ticket issuance, control `num_tickets`, rotate ticket keys, use callbacks, or impose their own certificate and policy lifetime checks.

## Fix Direction

Track an origin timestamp or cumulative expiration value for TLS 1.3 resumption chains that begin with a non-PSK handshake. When issuing a replacement ticket after resumption, cap the new ticket lifetime to the remaining cumulative budget, or decline to issue a new ticket once the budget is exhausted.

The budget should be configurable and should be able to account for peer certificate validity, revocation policy, and the elapsed time since the original online certificate authentication.
