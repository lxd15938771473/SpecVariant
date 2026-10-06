# Fresh-start deployments still accept 0-RTT while the replay window overlaps startup time


## Problem Description

wolfSSL's reachable TLS 1.3 0-RTT acceptance path does not implement the RFC 8446 Section 8.2 fresh-start overlap safeguard, and the behavior is observable at runtime. In two fresh-start scenarios that preserve the resumption material a deployment would realistically keep across restart, the restarted server still resumes the session and accepts early data immediately after startup instead of rejecting 0-RTT for the overlap period.

## Standard Requirement

- Official standard: [RFC 8446](https://www.rfc-editor.org/rfc/rfc8446.html)
- Section: `8.2 Client Hello Recording`
- Evidence: `RFC 8446, lines 5585-5587`

> When implementations are freshly started, they SHOULD reject 0-RTT as
>    long as any portion of their recording window overlaps the startup
>    time.

Interpretation:

When a server instance has just started, it should reject 0-RTT during any period where its anti-replay recording window still overlaps the startup time.

## Relevant Source Code

wolfSSL's 0-RTT admission path checks whether early data is possible, verifies ticket freshness, and enforces single-use eviction, but it does not consult any startup-time or replay-window-overlap state before accepting early data.

### `src/tls13.c:4615-4627`

```c
static int EarlyDataPossible(WOLFSSL* ssl)
{
    /* Need session resumption OR PSK callback configured */
    if (ssl->options.resuming) {
        return 1;
    }
#ifndef NO_PSK
    if (ssl->options.client_psk_tls13_cb != NULL ||
        ssl->options.client_psk_cb != NULL) {
        return 1;
    }
#endif
    return 0;
}
```

This entry point gates only on resumption state or PSK callback presence.

### `src/internal.c:41604-41617`

```c
        word32 ticketSeen;        /* Time ticket seen (ms) */

        ato32(psk->it->timestamp, &ticketSeen);

        now = TimeNowInMilliseconds();
        if (now == 0)
            return GETTIME_ERROR;
        /* Difference between now and time ticket constructed
         * (from decrypted ticket). */
        diff = now;
        diff -= ticketSeen;
        if (diff > timeout * 1000 ||
            diff > (sword64)TLS13_MAX_TICKET_AGE * 1000)
            return WOLFSSL_FATAL_ERROR;
```

The ticket freshness check compares current time to ticket construction time, but it does not compare startup time to the anti-replay recording window.

### `src/tls13.c:6908-6923`

```c
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
```

Actual 0-RTT acceptance depends on early-data enablement and single-use session eviction, with no startup-overlap guard in the acceptance condition.

## Runtime Evidence

A focused memio-based reproducer was built on top of wolfSSL's test harness to:

1. complete an initial TLS 1.3 handshake and obtain a 0-RTT-capable session;
2. destroy the original server object and construct a fresh server context;
3. preserve the resumption material that a real deployment could keep across restart;
4. immediately attempt resumption with early data on the restarted server.

`wolfSSL_get_early_data_status()` returns `2` for `WOLFSSL_EARLY_DATA_ACCEPTED`.

### Scenario 1: stateful ticket plus external session cache preserved across restart

- Fresh-start action: destroy the original server `WOLFSSL_CTX` and `WOLFSSL`, then create a new server context
- Persisted state: external session cache callbacks still retain the resumable session
- Observed output:

```text
stateful_restart: resumed=1 earlyWritten=26 earlyRead=0 earlyRet1=26 earlyRet2=0 clientStatus=2 serverStatus=2 cache(new=1 get=1 rem=1 has=0)
```

Interpretation:

- `resumed=1`: the restarted server resumed the session
- `earlyRet1=26`: the first server-side `wolfSSL_read_early_data()` consumed all 26 bytes of early data
- `clientStatus=2` and `serverStatus=2`: both sides report early data accepted

### Scenario 2: stateless ticket keys restored into a fresh server context

- Fresh-start action: destroy the original server context and create a new one
- Persisted state: export ticket keys with `wolfSSL_CTX_get_tlsext_ticket_keys()` and import them into the fresh context with `wolfSSL_CTX_set_tlsext_ticket_keys()`
- Observed output:

```text
stateless_restart_keys: resumed=1 earlyWritten=27 earlyRead=0 earlyRet1=27 earlyRet2=0 clientStatus=2 serverStatus=2
```

Interpretation:

- `resumed=1`: the restarted server resumed the session
- `earlyRet1=27`: the first server-side `wolfSSL_read_early_data()` consumed all 27 bytes of early data
- `clientStatus=2` and `serverStatus=2`: both sides report early data accepted

The final `earlyRead=0` in both lines is not a rejection signal. It reflects the second `wolfSSL_read_early_data()` call after the first one had already consumed the available early data.

## Inconsistency Reason

RFC 8446 says a freshly started implementation should reject 0-RTT while its anti-replay recording window overlaps startup time. wolfSSL instead resumes the session and accepts early data immediately after startup when the deployment preserves either the external session cache state or the session-ticket encryption keys across restart.

## Decision Reason

The source path lacks any startup-overlap check, and the focused runtime reproducer confirms that a freshly restarted server can still accept 0-RTT in realistic restart configurations. That is sufficient to upgrade this record from `suspected_issue` to `issue_found`.
