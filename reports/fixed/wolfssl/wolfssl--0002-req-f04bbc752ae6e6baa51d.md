# wolfSSL sends TLS 1.3 NewSessionTicket without client PSK mode advertisement

## Summary

This is a real but configuration-dependent protocol compliance and interoperability issue. RFC 9846 does not state an unconditional "server MUST NOT send NewSessionTicket" rule. The permission to send a TLS 1.3 `NewSessionTicket` is tied to the client having advertised suitable `psk_key_exchange_modes`, and servers are also advised not to send tickets that are incompatible with the advertised modes.

wolfSSL parses and stores `psk_key_exchange_modes`, but the automatic TLS 1.3 session-ticket send path and the explicit `wolfSSL_send_SessionTicket()` API do not check whether the client advertised suitable PSK key exchange modes before sending a ticket. A fresh runtime rerun confirmed that a non-PSK TLS 1.3 client can legally omit extension type `45`, while an unmodified ticket-enabled wolfSSL server still completes the handshake and sends `NewSessionTicket`.

## Standard Requirement

Official standard links:

- [RFC 9846 Section 4.3.9, Pre-Shared Key Exchange Modes](https://datatracker.ietf.org/doc/html/rfc9846#section-4.3.9)
- [RFC 9846 Section 4.7.1, New Session Ticket Message](https://datatracker.ietf.org/doc/html/rfc9846#section-4.7.1)
- [RFC 8446 Section 4.6.1, New Session Ticket Message](https://datatracker.ietf.org/doc/html/rfc8446#section-4.6.1)

RFC 9846 Section 4.3.9 makes `psk_key_exchange_modes` the client's statement of which PSK modes it supports. That statement applies both to PSKs offered in the current `ClientHello` and to PSKs that could be provided later by `NewSessionTicket`.

The same section says servers should not send tickets that are incompatible with the advertised modes. RFC 9846 Section 4.7.1 also makes the server's permission to send `NewSessionTicket` conditional on the client hello containing a suitable `psk_key_exchange_modes` extension.

The precise requirement is therefore:

1. A client that does not offer `pre_shared_key` may omit `psk_key_exchange_modes`; that omission is not itself a handshake error.
2. A `NewSessionTicket` creates a future resumption PSK, so the server should respect the client's advertised PSK mode support before sending one.
3. If the `ClientHello` contains no `psk_key_exchange_modes` extension, the server has no advertised mode set that makes the ticket suitable.

Compared with RFC 8446 Section 4.6.1, RFC 9846 adds PSK-mode conditions around ticket sending. This issue should be treated as a low-severity compliance and interoperability problem rather than a direct confidentiality or integrity break.

## Relevant Source Code

### `src/tls.c:12900-12944`

```c
static int TLSX_PskKeModes_Parse(WOLFSSL* ssl, const byte* input, word16 length,
                                 byte msgType)
{
    int    ret;
    byte modes;

    ret = TLSX_PskKeyModes_Parse_Modes(input, length, msgType, &modes);
    if (ret == 0)
        ret = TLSX_PskKeyModes_Use(ssl, modes);
    /* ... */
}

int TLSX_PskKeyModes_Use(WOLFSSL* ssl, byte modes)
{
    int ret = 0;
    TLSX* extension = TLSX_Find(ssl->extensions,
                                TLSX_PSK_KEY_EXCHANGE_MODES);
    if (extension == NULL) {
        ret = TLSX_Push(&ssl->extensions, TLSX_PSK_KEY_EXCHANGE_MODES,
                        NULL, ssl->heap);
        /* ... */
    }

    extension->val = modes;
    return 0;
}
```

When the client sends `psk_key_exchange_modes`, wolfSSL stores an extension object and the parsed mode bits in `ssl->extensions`. If the client does not send the extension, that receive-state object is not created, so the server has enough state to distinguish "extension present" from "extension absent".

### `src/tls13.c:6942-6948`

```c
/* Get the PSK key exchange modes the client wants to negotiate. */
ext = TLSX_Find(ssl->extensions, TLSX_PSK_KEY_EXCHANGE_MODES);
if (ext == NULL) {
    WOLFSSL_ERROR_VERBOSE(MISSING_HANDSHAKE_DATA);
    return MISSING_HANDSHAKE_DATA;
}
modes = ext->val;
```

The handshake branch that actually uses PSK requires this extension. That protects the current PSK handshake path, but it does not protect the later certificate-handshake path that sends `NewSessionTicket`.

### `src/internal.c:2883-2894`

```c
#if defined(HAVE_SESSION_TICKET) && !defined(NO_WOLFSSL_SERVER)
    ctx->ticketEncCb = DefTicketEncCb;
    ctx->ticketEncCtx = (void*)&ctx->ticketKeyCtx;
    ctx->ticketHint = SESSION_TICKET_HINT_DEFAULT;
#if defined(WOLFSSL_TLS13)
    ctx->maxTicketTls13 = 1; /* default to sending a session ticket if compiled
                                in */
#endif
```

In ticket-enabled TLS 1.3 server builds, the default context installs a ticket encryption callback and defaults to sending one TLS 1.3 session ticket.

### `src/tls13.c:13037-13217`

```c
static int SendTls13NewSessionTicket(WOLFSSL* ssl)
{
    if (DefTicketHintTooLarge(ssl)) {
        return 0;
    }
    /* ... */
    if (!ssl->options.noTicketTls13) {
        if ((ret = SetupTicket(ssl)) != 0)
            return ret;
        if ((ssl->options.mask & WOLFSSL_OP_NO_TICKET) == 0) {
            if ((ret = CreateTicket(ssl)) != 0)
                return ret;
        }
    }
    /* ... build and send NewSessionTicket ... */
    ret = SendBuffered(ssl);
}
```

The shared ticket construction and send function does not centrally check the client's PSK key exchange modes. All callers inherit that missing precondition.

### `src/tls13.c:16085-16100`

```c
#ifdef HAVE_SESSION_TICKET
while (ssl->options.ticketsSent < ssl->options.maxTicketTls13) {
    if (!ssl->options.noTicketTls13 && ssl->ctx->ticketEncCb != NULL) {
        if ((ssl->error = SendTls13NewSessionTicket(ssl)) != 0) {
            WOLFSSL_ERROR(ssl->error);
            return WOLFSSL_FATAL_ERROR;
        }
    }
    ssl->options.ticketsSent++;
    /* ... */
}
#endif
```

The automatic send loop checks only the ticket switch, encryption callback, and count. It does not check whether `TLSX_PSK_KEY_EXCHANGE_MODES` exists or whether it contains a compatible mode.

### `src/tls13.c:16151-16166`

```c
int wolfSSL_send_SessionTicket(WOLFSSL* ssl)
{
    if (ssl == NULL || !IsAtLeastTLSv1_3(ssl->version))
        return BAD_FUNC_ARG;
    if (ssl->options.side == WOLFSSL_CLIENT_END)
        return SIDE_ERROR;
    if (ssl->options.handShakeState != HANDSHAKE_DONE)
        return NOT_READY_ERROR;

    if ((ssl->error = SendTls13NewSessionTicket(ssl)) != 0) {
        WOLFSSL_ERROR(ssl->error);
        return WOLFSSL_FATAL_ERROR;
    }
    ssl->options.ticketsSent++;
    return WOLFSSL_SUCCESS;
}
```

The explicit API checks version, endpoint role, and handshake state, but it also lacks the `psk_key_exchange_modes` precondition.

## Implementation Behavior

In a ticket-enabled TLS 1.3 server build:

1. A non-PSK certificate handshake can legally omit both `pre_shared_key` and `psk_key_exchange_modes`.
2. wolfSSL accepts that `ClientHello`; only the branch that negotiates PSK requires `TLSX_PSK_KEY_EXCHANGE_MODES`.
3. After the handshake completes and the server receives client Finished, the automatic ticket loop calls `SendTls13NewSessionTicket()` based on `maxTicketTls13` and `ticketEncCb`.
4. `SendTls13NewSessionTicket()` creates and sends a `NewSessionTicket` without checking whether the client advertised a suitable PSK mode.
5. `wolfSSL_send_SessionTicket()` reaches the same internal send function and has the same behavior.

`NO_PSK` does not remove this path. The rerun build defined `NO_PSK`, `HAVE_SESSION_TICKET`, and `WOLFSSL_TLS13`, and the server still sent a TLS 1.3 session ticket that creates a future resumption PSK.

## Inconsistency Reason

The standard model is: the client first advertises acceptable PSK exchange modes, and the server then uses those modes to decide whether a future resumption PSK is suitable. wolfSSL implements: if tickets are enabled, the handshake state is suitable, and the ticket callback is available, send `NewSessionTicket`.

Successful completion of a non-PSK certificate handshake does not prove that the client advertised acceptable future PSK modes. wolfSSL's default client usually sends extension `45`, which hides this missing server-side pre-send check in common interop runs. The targeted runtime rerun shows that when a client legally omits the extension, the unmodified server still sends `NewSessionTicket`.

## Runtime Evidence

Fresh rerun date: 2026-08-10.

Action: a ticket-enabled unmodified wolfSSL TLS 1.3 server was run with a transparent proxy that parsed the cleartext `ClientHello` extension list. The reproducer client performed a non-PSK initial handshake and was built so it did not advertise extension type `45` (`psk_key_exchange_modes`).

Observed result for the reproducer:

```json
{
  "client_exit_code": 0,
  "server_exit_code": 0,
  "client_hello_extension_ids": [43, 13, 10, 22, 51, 35],
  "client_hello_contains_psk_key_exchange_modes": false,
  "new_session_ticket_callback_observed": true,
  "issue_observed": true,
  "probe_passed": true,
  "ticket_marker": "Session Ticket CB: ticketSz = 210, ctx = initial session"
}
```

Interpretation: the `ClientHello` did not contain extension type `45`; the TLS 1.3 handshake still succeeded; and the unmodified ticket-enabled wolfSSL server sent `NewSessionTicket`.

Action: the same proxy was run as a positive control with the default wolfSSL client, which should advertise extension type `45`.

Observed result for the control:

```json
{
  "client_exit_code": 0,
  "server_exit_code": 0,
  "client_hello_extension_ids": [45, 43, 13, 10, 22, 51, 35],
  "client_hello_contains_psk_key_exchange_modes": true,
  "new_session_ticket_callback_observed": true,
  "issue_observed": false,
  "probe_passed": true,
  "ticket_marker": "Session Ticket CB: ticketSz = 210, ctx = initial session"
}
```

Interpretation: when extension `45` was present, the proxy recognized it. This rules out the explanation that the reproducer merely failed to detect the extension. Both client and server exited with code `0`, so the observed ticket came from a successful TLS 1.3 handshake, not from a failed path or an anomalous log.

## Impact

- wolfSSL deviates from RFC 9846's ticket-sending permission and ticket-mode compatibility guidance.
- A server can provide a resumption PSK to a client that did not advertise any acceptable PSK exchange mode, causing useless ticket state, extra network and storage cost, and possible later resumption failures or inconsistent behavior.
- The rerun did not show a direct confidentiality or integrity break; the main risk is protocol compliance and interoperability.
- Builds without `HAVE_SESSION_TICKET` have no reachable send path and are out of scope.

## Fix Direction

Put the check in shared pre-send logic used by all `NewSessionTicket` callers:

1. After `ClientHello` parsing, retain whether `psk_key_exchange_modes` was received and which mode bits were recognized.
2. Send automatic `NewSessionTicket` only when at least one advertised mode is compatible with the server's ticket and resumption policy.
3. Make `wolfSSL_send_SessionTicket()` use the same check; when the extension is absent or incompatible, return a clear documented error.
4. Add regression tests for absent extension, only unknown modes, only `psk_ke`, only `psk_dhe_ke`, both modes, and the explicit API path.
