# Confirmed mismatch in sending tickets incompatible with advertised PSK modes

## Verdict

- Verdict: `confirmed_issue`
- Confidence: `high`
- Covered Record IDs: `cand-c9630081020f-baseline`, `cand-31a3c8949ed7-state-order`, `cand-c5b2fe934951-missing`
- Root Cause Key: `req-bd8f4cad610f188e8b0b`
- Recheck Date: `2026-08-03`

## Problem Description

A focused runtime recheck confirmed a real RFC 8446 Section 4.2.9 mismatch in wolfSSL.

When the client advertises only `psk_ke` and the server is configured to only allow `psk_dhe_ke` for PSK-based handshakes, wolfSSL still completes the initial full TLS 1.3 certificate handshake and sends a `NewSessionTicket`. That ticket is not compatible with the only advertised mode: a follow-up resumption attempt with the saved ticket fails on the server PSK path with `PSK_KEY_ERROR`.

This report deduplicates multiple candidate-level records that resolved to the same root cause.

## Standard Requirement

- Official standard: [RFC 8446](https://www.rfc-editor.org/rfc/rfc8446.html)
- Requirement ID: `req-bd8f4cad610f188e8b0b`
- Section: Section 4.2.9 Pre-Shared Key Exchange Modes (lines 2862-2864)

> Servers SHOULD NOT
>    send NewSessionTicket with tickets that are not compatible with the
>    advertised modes;

Interpretation:

If the client advertises a PSK mode set and the server later sends `NewSessionTicket`, the ticket should be usable with at least one advertised mode. A ticket that is incompatible with every advertised mode should not be sent.

The key context from RFC 8446 Section 4.2.9 is that `psk_key_exchange_modes`
does not only constrain PSKs already offered in the current `ClientHello`; it
also constrains PSKs that the server may later create with `NewSessionTicket`.

## Relevant Source Code

The runtime reproducer matches a concrete source path:

### `src/tls.c:16724-16735`

ClientHello mode advertisement is built directly from the `noPskDheKe` / `onlyPskDheKe` options. With `wolfSSL_CTX_no_dhe_psk()` on the client, the extension advertises only `PSK_KE`.

```c
/* Some servers do not generate session tickets unless
 * the extension is seen in a non-resume client hello.
 * We used to send it only if we were otherwise using PSK.
 * Now always send it.
 */
/* Pre-shared key modes: mandatory extension for resumption. */
if (!ssl->options.onlyPskDheKe) {
    modes = 1 << PSK_KE;
}
if (!ssl->options.noPskDheKe) {
    modes |= 1 << PSK_DHE_KE;
}
```

### `src/tls13.c:13097-13217`

`SendTls13NewSessionTicket()` creates and sends the ticket after the handshake, but does not check whether the current client's advertised PSK mode set can use that ticket.

```c
if (!ssl->options.noTicketTls13) {
    if ((ret = SetupTicket(ssl)) != 0)
        return ret;
    if ((ssl->options.mask & WOLFSSL_OP_NO_TICKET) == 0) {
        if ((ret = CreateTicket(ssl)) != 0)
            return ret;
    }
}
...
ret = SendBuffered(ssl);
```

### `src/tls13.c:7007-7039`

On the resumption path, a server configured with `wolfSSL_CTX_only_dhe_psk()` rejects a client that did not advertise `PSK_DHE_KE`.

```c
if (((modes & (1 << PSK_DHE_KE)) != 0 && !ssl->options.noPskDheKe &&
     ext != NULL)) {
    ...
}
else if (ssl->options.onlyPskDheKe ||
         (ssl->options.failNoPSK && !ssl->options.resuming)) {
    return PSK_KEY_ERROR;
}
else {
    if ((modes & (1 << PSK_KE)) == 0) {
        WOLFSSL_ERROR_VERBOSE(PSK_KEY_ERROR);
        return PSK_KEY_ERROR;
    }
    ssl->options.noPskDheKe = 1;
}
```

### `src/tls13.c:15149-15183`

`wolfSSL_CTX_only_dhe_psk()` is not a one-off runtime accident during the
second handshake; it is an explicit server policy stating that PSK handshakes
must use `(EC)DHE`.

```c
/* Only allow (EC)DHE key exchange when using pre-shared keys. */
int wolfSSL_CTX_only_dhe_psk(WOLFSSL_CTX* ctx)
{
    ...
    ctx->onlyPskDheKe = 1;
    ...
}
```

## Runtime Evidence

The focused native client/server recheck on `2026-08-03` performed an initial full handshake, captured the delivered session ticket, and attempted a second handshake with that ticket under compatible and incompatible PSK-mode advertisements. It observed:

- Compatible control: `compatible_only_dhe`
  - Client used default PSK mode advertisement.
  - Server used `wolfSSL_CTX_only_dhe_psk()`.
  - First handshake completed and delivered `1` ticket of size `210`.
  - Second handshake resumed successfully: `second_session_reused=1`.

- Incompatible reproducer: `incompatible_only_dhe`
  - Client used `wolfSSL_CTX_no_dhe_psk()`, so it advertised only `psk_ke`.
  - Server used `wolfSSL_CTX_only_dhe_psk()`.
  - First handshake still completed and delivered `1` ticket of size `210`.
  - Second handshake using the saved ticket failed: client error `FATAL_ERROR` (`-313`), server error `PSK_KEY_ERROR` (`-333`).

Observed log excerpt:

```text
compatible_only_dhe: first_handshake_ok first_ticket_count=1 first_ticket_size=210
compatible_only_dhe: first_ticket_count=1 first_ticket_size=210 second_session_reused=1
incompatible_only_dhe: first_handshake_ok first_ticket_count=1 first_ticket_size=210
incompatible_only_dhe: second handshake failed client_err=-313 server_err=-333
```

The positive control shows the harness can successfully resume an `only_dhe_psk` ticket when the client advertises a compatible mode. The reproducer shows the same server configuration still sends a ticket after a `psk_ke`-only advertisement even though that ticket cannot later be used.

## Inconsistency Reason

RFC 8446 says the server should not send a `NewSessionTicket` whose ticket is incompatible with the advertised modes.

wolfSSL does the opposite in the reproduced scenario:

1. The client advertises only `psk_ke`.
2. The server is configured so PSK use requires `psk_dhe_ke`.
3. The initial certificate handshake succeeds and the server still sends a ticket.
4. That ticket cannot be resumed under the advertised mode set and triggers `PSK_KEY_ERROR`.

So the runtime behavior proves the ticket was incompatible with every advertised mode at the time it was sent.

## Decision Reason

This is a real issue, not a static-only concern. The recheck added both:

- a positive control showing the harness and server configuration can resume tickets when the modes are compatible, and
- a concrete reproducer showing wolfSSL still sends a ticket after a `psk_ke`-only advertisement even though the ticket is unusable.

## Remaining Uncertainty

Low.

The factual behavior is confirmed by runtime execution. The remaining discussion is only about severity and remediation priority: the RFC text here is `SHOULD NOT`, but the incompatibility and the unnecessary ticket emission are real.
