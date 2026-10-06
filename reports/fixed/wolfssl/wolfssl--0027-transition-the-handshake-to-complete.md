
# Premature public handshake-complete signal before client Finished is emitted

## Verdict

- Verdict: `issue_found`
- Confidence: `high`
- Covered candidates: `cand-2c8f0e3bfb14-baseline`, `cand-13dc8a551574-replay`, `cand-604cff8ff591-state-order`, `cand-abc287c758f1-duplicate`
- Covered requirements: `req-819e857dbe9e186e595d`

## Problem Description

The original suspicion is confirmed, but the scope is now more precise.

On the TLS 1.3 client path, if the final client Finished write hits `WOLFSSL_ERROR_WANT_WRITE`, wolfSSL sets the public handshake-done state before the Finished record has actually been accepted by the transport callback and delivered to the peer-facing buffer. As a result, `wolfSSL_is_init_finished()` can return true while the client's Finished is still unsent.

This is a real externally observable issue, but it is narrower than "the whole handshake fully succeeds early":

- The public completion API flips early.
- The internal TLS 1.3 client state machine has not yet reached `FINISHED_DONE`.
- In the reproduced scenario, application data does not bypass the pending Finished record; the record remains buffered until writes resume.

Covered variants:

- `cand-2c8f0e3bfb14-baseline`: baseline - Direct normative probe: transition the handshake to complete
- `cand-13dc8a551574-replay`: replay - Replay the input on a fresh connection: transition the handshake to complete
- `cand-604cff8ff591-state-order`: state_order - Deliver the input in an adjacent invalid state: transition the handshake to complete
- `cand-abc287c758f1-duplicate`: duplicate - Duplicate one element or message: transition the handshake to complete

## Standard Requirement

- Official standard: [RFC 8446](https://www.rfc-editor.org/rfc/rfc8446.html)
### Requirement 1

- Requirement ID: `req-819e857dbe9e186e595d`
- Section: RFC 8446 Section 2 Protocol Overview (lines 699-702)

Relevant context:

> The client responds with its own Authentication messages, namely Certificate and CertificateVerify (if requested), and Finished. At this point, the handshake is complete,

What this requires here:

- In the full TLS 1.3 handshake, the completion point comes after the client sends its Finished message.
- Before the client Finished is sent, the peer has not yet received the client's final handshake authentication/key-confirmation message.
- Therefore an implementation must not expose the full handshake as complete before the client Finished has actually been sent.

Why this applies to this report:

- The issue is not about an internal staging detail in isolation.
- The issue is that a public API, `wolfSSL_is_init_finished()`, is documented and implemented as a connection-established/handshake-complete signal.
- When the final transport write returns `WANT_WRITE`, the client's Finished has not yet been accepted by the transport callback, so the RFC completion point has not yet been reached.

## Relevant Source Code

The completion signal is split across three layers:

- `SendTls13Finished()` marks the handshake done.
- `wolfSSL_is_init_finished()` exposes that state as the public completion signal.
- `SendBuffered()` may still fail with `WANT_WRITE`, leaving the Finished record buffered and unsent.

### `src/ssl_api_hs.c:1294-1310`

```text
    /* return true if connection established */
    int wolfSSL_is_init_finished(const WOLFSSL* ssl)
    {
        ...
        if (ssl->options.handShakeState == HANDSHAKE_DONE)
            return 1;
```

The public API is explicitly described as a connection-established check and returns true directly from `handShakeState == HANDSHAKE_DONE`.

### `src/tls13.c:12369-12392`

```text
#ifndef NO_WOLFSSL_CLIENT
    if (ssl->options.side == WOLFSSL_CLIENT_END) {
        ssl->options.clientState = CLIENT_FINISHED_COMPLETE;
        ssl->options.handShakeState = HANDSHAKE_DONE;
        ssl->options.handShakeDone  = 1;
    }
#endif
...
    if ((ret = SendBuffered(ssl)) != 0)
        return ret;
```

On the client path, `SendTls13Finished()` sets the handshake-done flags before it tries to flush the Finished record through `SendBuffered()`.

### `src/internal.c:11748-11765`

```text
    while (ssl->buffers.outputBuffer.length > 0) {
        ...
        if (sent < 0) {
            switch (sent) {
                case WC_NO_ERR_TRACE(WOLFSSL_CBIO_ERR_WANT_WRITE):
                    ...
                    return WC_NO_ERR_TRACE(WANT_WRITE);
```

If the transport callback reports `WANT_WRITE`, `SendBuffered()` returns `WANT_WRITE` and leaves the pending bytes in `ssl->buffers.outputBuffer`.

### `src/tls13.c:14774-14803`

```text
        case FIRST_REPLY_FOURTH:
            if ((ssl->error = SendTls13Finished(ssl)) != 0) {
                ...
                return WOLFSSL_FATAL_ERROR;
            }
            WOLFSSL_MSG("sent: finished");
            ...
            ssl->options.connectState = FINISHED_DONE;
            WOLFSSL_MSG("connect state: FINISHED_DONE");
```

The outer TLS 1.3 client state machine does not advance to `FINISHED_DONE` until `SendTls13Finished()` returns success. Therefore the public handshake-done flag can become true before the outer connect state machine reaches its own completion state.

## Runtime Evidence

### Round 1: Original user-visible reproducer

- Status: `passed`
- Positive control: `passed`
- Reproducer: `passed`

The original memio probe reproduced the user-visible timing gap directly by forcing the client's final TLS 1.3 write to return `WANT_WRITE`.

Observed result:

- Before the blocked final client step: `client_is_init_finished=0`, `s_len=0`, `s_msgs=0`
- Blocked final client step: `ret=-1`, `err=3` (`WOLFSSL_ERROR_WANT_WRITE`), `is_init_finished=1`, `s_len_before=0`, `s_len_after=0`, `s_msgs_before=0`, `s_msgs_after=0`
- Resume after re-enabling writes: `ret=1`, `is_init_finished=1`, `s_len=74`, `s_msgs=1`

This shows the public completion API flipping to true while no client Finished bytes have yet been delivered into the server-bound memio buffer.

### Round 2: Deeper state/buffer recheck

- Status: `passed`
- Goal: distinguish "public API flips early" from stronger claims such as "connect succeeds early" or "application data bypasses Finished"

A second probe inspected both the public state and the internal output buffer in the blocked step.

Observed result:

- Blocked final client step:
  `ret=-1`, `err=3`, `is_init_finished=1`, `handShakeDone=1`, `handShakeState=16` (`HANDSHAKE_DONE`), `connectState=8` (`FIRST_REPLY_FOURTH`), `out_len=74`, `s_len=0`, `s_msgs=0`
- Write attempt while still blocked:
  `wolfSSL_write()` also returned `WANT_WRITE`, and the state remained `out_len=74`, `s_len=0`
- Resume after re-enabling writes:
  `ret=1`, `err=0`, `connectState=9` (`FINISHED_DONE`), `out_len=0`, `s_len=74`, `s_msgs=1`

What this proves:

- At the moment `wolfSSL_is_init_finished()` becomes true, the Finished record is still sitting in the internal output buffer (`out_len=74`).
- No Finished bytes have yet reached the peer-facing memio buffer (`s_len=0`).
- The outer client connect state has not yet advanced to `FINISHED_DONE`.
- In this reproduced path, application data does not leapfrog the pending Finished record; writes remain blocked behind the same buffered record.

## Inconsistency Reason

- RFC 8446 places the handshake-complete point after the client sends Finished in the full handshake.
- wolfSSL's TLS 1.3 client send path sets `handShakeState = HANDSHAKE_DONE` and `handShakeDone = 1` before `SendBuffered()` has successfully emitted the Finished record.
- `wolfSSL_is_init_finished()` exposes that early flag as a public "connection established" result.
- When the underlying write returns `WANT_WRITE`, the public completion signal says "done" while the Finished record is still buffered and unsent.

## Decision Reason

- The standard defines the completion point after the client Finished is sent.
- The implementation sets the public completion flag before the transport flush succeeds.
- The runtime repro confirms that `wolfSSL_is_init_finished()` becomes true while the peer-facing transport buffer still has zero Finished bytes and 74 bytes remain in wolfSSL's internal output buffer.
- The deeper repro also shows that the outer client state machine is still pre-`FINISHED_DONE` at that moment.

That is enough to classify this as `issue_found`: wolfSSL exposes a premature public handshake-complete signal on the TLS 1.3 client path under `WANT_WRITE`.

## Remaining Uncertainty

- None on the core finding.
- The scope is now clearer: this report confirms a premature public completion signal, not an early `wolfSSL_connect()` success return and not an observed bypass of Finished by application data in the reproduced path.
