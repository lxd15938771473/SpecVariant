# Confirmed RFC 8446 Parse-Error Mismatch: Zero-Length NewSessionTicket.ticket Is Accepted

## Verdict

- Verdict: `issue_found`
- Confidence: `high`
- Covered Record IDs: `cand-a9cff2a6bf82-baseline`, `cand-49612dfb1b81-replay`, `cand-98a55560dfe0-boundary`, `cand-c08a16ebdc2f-missing`
- Root Cause Key: `zero-length-ticket-accepted`
- Scope: TLS 1.3 client-side parsing of post-handshake `NewSessionTicket`

## Problem Description

wolfSSL's TLS 1.3 client accepts a post-handshake `NewSessionTicket` whose encoded
`ticket` vector length is `0`.

That behavior is inconsistent with RFC 8446 for two separate reasons:

1. The `NewSessionTicket` structure defines `ticket` as `opaque ticket<1..2^16-1>`,
   so an empty ticket is outside the legal wire syntax.
2. RFC 8446's generic parse-error rule says that a peer receiving an
   `out-of-range length` MUST terminate the connection with `decode_error`.

The current implementation does neither. It accepts the message, stores
`ticketLen == 0`, and continues.

This is best described as a malformed-input rejection bug in the TLS 1.3 client.
Later resumption paths generally require `ticketLen > 0`, so the strongest
confirmed claim is not "empty ticket successfully resumes", but rather
"malformed `NewSessionTicket` is accepted instead of rejected with `decode_error`".

## Standard Requirement

- Official standard: [RFC 8446](https://www.rfc-editor.org/rfc/rfc8446.html)
- Requirement ID: `req-68ce964bf479c18c1a5f`

### RFC 8446 Section 4.6.1: NewSessionTicket syntax

- Section link: [RFC 8446 Section 4.6.1](https://www.rfc-editor.org/rfc/rfc8446.html#section-4.6.1)

> struct {
>     uint32 ticket_lifetime;
>     uint32 ticket_age_add;
>     opaque ticket_nonce<0..255>;
>     opaque ticket<1..2^16-1>;
>     Extension extensions<0..2^16-2>;
> } NewSessionTicket;

### RFC 8446 presentation language: `<floor..ceiling>` length semantics

- Section link: [RFC 8446 Section 3.4](https://www.rfc-editor.org/rfc/rfc8446.html#section-3.4)

> Variable-length vectors are defined by specifying a subrange of legal lengths
> ... "mandatory" ... can never be empty.

This makes `ticket<1..2^16-1>` a non-empty vector by definition. An encoded
actual length of `0` is outside the legal range.

### RFC 8446 parse-error rule

- Section link: [RFC 8446 Section 6.2](https://www.rfc-editor.org/rfc/rfc8446.html#section-6.2)

> Peers which receive a message which cannot be parsed according to the syntax
> (e.g., ... contain an out-of-range length) MUST terminate the connection with
> a "decode_error" alert.

### Interpretation

- `NewSessionTicket.ticket` is not allowed to be empty on the wire.
- Receiving `ticket_len = 0` is a syntax violation, not merely a policy choice.
- A compliant receiving peer must abort with `decode_error`, not silently accept
  and cache the ticket.

## Relevant Source Code

### `src/tls13.c:12837-12847`

The parser reads the 16-bit `ticket` length, checks only that the claimed length
fits within the message boundary, and then forwards the value directly to
`SetTicket()`:

```c
/* Ticket length. */
if ((*inOutIdx - begin) + LENGTH_SZ > size)
    return BUFFER_ERROR;
ato16(input + *inOutIdx, &length);
*inOutIdx += LENGTH_SZ;
if ((*inOutIdx - begin) + length > size)
    return BUFFER_ERROR;

if ((ret = SetTicket(ssl, input + *inOutIdx, length)) != 0)
    return ret;
*inOutIdx += length;
```

There is no explicit rejection of `length == 0`.

### `src/internal.c:36841-36868`

`SetTicket()` rejects only lengths above the 16-bit maximum. It records
`ticketLen = 0` as a valid state:

```c
int SetTicket(WOLFSSL* ssl, const byte* ticket, word32 length)
{
    word32 sessIdLen = ID_LEN;

    if (length > WOLFSSL_MAX_16BIT)
        return BUFFER_ERROR;

    if (!HaveUniqueSessionObj(ssl))
        return MEMORY_ERROR;

    ...
    ssl->session->ticketLen = (word16)length;

    if (length > 0) {
        ...
    }

    return 0;
}
```

### Stored but not normally reused as a ticket

Two later paths show why this is still primarily a malformed-input acceptance
issue rather than a confirmed empty-ticket resumption success:

- `src/tls13.c:5776` gates some TLS 1.3 session-secret callback behavior on
  `ssl->session->ticketLen > 0`
- `src/internal.c:32898-32902` only offers a session ticket during resumption
  if `ssl->options.resuming && ssl->session->ticketLen > 0`

So the confirmed defect is that an invalid post-handshake ticket is accepted and
stored, not that normal resumption later proceeds with a zero-length ticket.

## Runtime Evidence

## Round 1: Existing family reproducer

- Status: `passed`
- Positive control: `passed`
- Reproducer: `passed`

The original source-backed reproducer for this requirement family reported
`zero_length_newsessionticket_ticket_accepted` and identified the same root
cause from parser flow and stored state.

### Step: `tls13_positive_control`

The positive control completed a normal TLS 1.3 handshake and processed the server's valid post-handshake ticket. It exited successfully.

### Step: `family_reproducer`

The family reproducer followed the parser and stored-session state after supplying a zero-length ticket. It exited successfully and reported `zero_length_newsessionticket_ticket_accepted`.

## Round 2: Focused runtime recheck on August 3, 2026

- Status: `passed`
- Goal: confirm that a live TLS 1.3 client path really accepts an encrypted
  post-handshake `NewSessionTicket` with `ticket_len = 0`

This recheck used a focused memio unit-test probe:

1. complete a normal TLS 1.3 handshake;
2. drain the genuine server-sent `NewSessionTicket`;
3. inject an encrypted crafted `NewSessionTicket` with:
   - `ticket_lifetime = 3600`
   - `ticket_age_add = 0x01020304`
   - `ticket_nonce` length `1`, value `0xA0`
   - `ticket` length `0`
   - empty extensions;
4. observe whether the client aborts or accepts the malformed message.

### Step: `focused_zero_length_ticket_probe`

The focused unit test ran `test_tls13_zero_length_session_ticket_probe` against the audited TLS 1.3 build and exited with code `0`.

Observed result:

- the probe passed only if the client consumed the crafted message;
- after the read, the probe observed `ssl_c->session->ticketLen == 0`;
- it also observed `ticketAdd == 0x01020304`;
- and `ticketNonce.len == 1` with first nonce byte `0xA0`.

This is direct runtime evidence that the malformed encrypted `NewSessionTicket`
was accepted instead of causing `decode_error`.

### Step: `new_session_ticket_max_lifetime_control`

The same unit-test binary ran `test_tls13_new_session_ticket_max_lifetime` as a negative control and exited with code `0`.

Purpose:

- confirm the same test environment still detects malformed `NewSessionTicket`
  fields when an existing parser-side check should fire.

Observed result:

- the over-limit `ticket_lifetime` control test passed, providing positive
  evidence that the unit-test environment is healthy and that parser checks do
  trigger when implemented.

## Inconsistency Reason

- RFC 8446 defines `NewSessionTicket.ticket` as a non-empty vector and requires
  `decode_error` on out-of-range lengths.
- wolfSSL's TLS 1.3 client checks only message-boundary framing for this field.
- a `ticket_len = 0` value therefore reaches `SetTicket()`, which records
  `ticketLen == 0` instead of rejecting the message.

## Decision Reason

- The standard basis is now stronger than "Appendix B says the lower bound is 1":
  the main `NewSessionTicket` syntax, the presentation-language rules, and the
  generic parse-error rule together make the receiving-side obligation clear.
- Source review shows the exact missing check in the parser and the accepting
  behavior in `SetTicket()`.
- A focused runtime recheck on August 3, 2026 confirmed the malformed encrypted
  post-handshake message is accepted on a live TLS 1.3 client path.

Therefore this report should remain `issue_found`.
