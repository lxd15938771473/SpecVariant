# wolfSSL does not ignore update_requested after reaching the KeyUpdate send limit

## Summary

The conclusion needs to be layered. wolfSSL satisfies the hard RFC 9846 Section 4.7.3 `MUST NOT` requirement not to let the number of KeyUpdates exceed `2^48-1`, because the send path refuses an over-limit KeyUpdate before it emits a network record.

The real issue is the adjacent `SHOULD` behavior. When the local send count has already reached `2^48-1` and the peer sends a valid `KeyUpdate(update_requested)`, the default non-`WOLFSSL_RW_THREADED` receive path does not ignore the request flag and continue. It tries to send a response, hits the send-limit guard, exposes `BAD_STATE_E` through `wolfSSL_read()`, and leaves the connection in a persistent error state.

This should be reported as a `SHOULD`-level implementation deviation, not as a hard `MUST` violation. The trigger requires the local endpoint to have already sent `2^48-1` KeyUpdates, so practical severity is low.

## Standard Requirement

Official standard link:

- [RFC 9846 Section 4.7.3, Key and Initialization Vector Update](https://datatracker.ietf.org/doc/html/rfc9846#section-4.7.3)

RFC 9846 Section 4.7.3 requires sending implementations not to allow the epoch, and therefore the number of key updates, to exceed `2^48-1`. It also says that if a sender receives `KeyUpdate(update_requested)` and sending its own response would exceed these limits, it must not send that response and should instead ignore the `update_requested` flag.

The relevant decisions are:

- The sender must not send an over-limit KeyUpdate.
- The receiver must not enforce the peer's counter limit.
- If responding would exceed the local limit, the implementation should treat `update_requested` as not requiring a response and continue until Section 5.5 key-usage limits require rekeying or closure.

Failing to implement the recommended ignore behavior is not a hard `MUST` violation. However, turning a valid peer request into persistent `BAD_STATE_E` without a documented reason is still a reportable recommended-behavior deviation.

## Relevant Source Code

### Send path refuses an over-limit KeyUpdate

`src/tls13.c:12432-12442`

```c
if (!ssl->options.dtls) {
    /* RFC 9846 Section 4.7.3: a sending implementation MUST NOT allow its
     * number of key updates to exceed 2^48-1. Receivers MUST NOT enforce
     * this on the peer. */
    if (w64GTE(ssl->keys.keyUpdateCount,
               w64From32(TLS13_KEY_UPDATE_MAX_HI32,
                         TLS13_KEY_UPDATE_MAX_LO32))) {
        WOLFSSL_MSG("TLS 1.3 key update count at maximum; refusing "
                    "KeyUpdate");
        return BAD_STATE_E;
    }
}
```

This check happens before the KeyUpdate record is built or sent, so wolfSSL does not violate the hard over-limit send prohibition.

### Receiving update_requested always sets response state

`src/tls13.c:12551-12560`

```c
switch (input[i]) {
    case update_not_requested:
        ssl->keys.keyUpdateRespond = 0;
        ssl->keys.updateResponseReq = 0;
        break;
    case update_requested:
        ssl->keys.keyUpdateRespond = 1;
        break;
```

This branch does not check whether the local send counter is already at `2^48-1`. It converts the peer request into pending response state.

### Receive keys are updated normally

`src/tls13.c:12566-12575`

```c
*inOutIdx += totalSz;

if ((ret = DeriveTls13Keys(ssl, update_traffic_key, DECRYPT_SIDE_ONLY, 1))
                                                                     != 0) {
    return ret;
}
if ((ret = SetKeysSide(ssl, DECRYPT_SIDE_ONLY)) != 0)
    return ret;
```

wolfSSL processes the peer KeyUpdate and installs new receive keys. The defect is in whether and how it responds.

### Default path immediately tries to send the response

`src/tls13.c:12597-12629`

```c
if (ssl->keys.keyUpdateRespond) {
#ifndef WOLFSSL_RW_THREADED
    return SendTls13KeyUpdate(ssl);
#else
    ssl->options.sendKeyUpdate = 1;
    return 0;
#endif
}
```

The tested build did not define `WOLFSSL_RW_THREADED` or `HAVE_WRITE_DUP`, so the receive path returned the result from `SendTls13KeyUpdate()` directly.

### keyUpdateRespond is cleared only after the send-limit check

`src/tls13.c:12461-12469`

```c
ssl->keys.updateResponseReq = output[i++] =
     !ssl->keys.updateResponseReq && !ssl->keys.keyUpdateRespond;
ssl->keys.keyUpdateRespond = 0;
```

Because the send-limit check returns before this block when the counter is at `2^48-1`, `keyUpdateRespond` is not cleared.

### Persistent non-retryable error state

`src/internal.c:24206-24222`

```c
if (ssl->error != 0 &&
    ssl->error != WC_NO_ERR_TRACE(WANT_READ) &&
    ssl->error != WC_NO_ERR_TRACE(WANT_WRITE)) {
    WOLFSSL_MSG("ProcessReply retry in error state, not allowed");
    return ssl->error;
}
```

`BAD_STATE_E` is not a retryable state. Once it enters the connection state, later `wolfSSL_read()` calls continue to fail.

## Implementation Behavior

In the tested default TLS 1.3 build:

1. The local send count was staged at `2^48-1`.
2. The peer sent a real encrypted `KeyUpdate(update_requested)`.
3. wolfSSL parsed the peer KeyUpdate and installed new receive keys.
4. `keyUpdateRespond` was set to `1`.
5. The default receive path immediately called `SendTls13KeyUpdate()`.
6. The send function saw the counter was already at the limit and returned `BAD_STATE_E` before emitting a response record.
7. `wolfSSL_read()` returned failure to the application and `keyUpdateRespond` remained set.
8. Even after the peer sent decryptable application data, the next local read returned `BAD_STATE_E` again.

This proves that wolfSSL does not send an over-limit KeyUpdate, but also does not ignore `update_requested` and continue as RFC 9846 recommends.

## Inconsistency Reason

RFC 9846 requires the endpoint not to send a KeyUpdate if doing so would exceed the local update limit. wolfSSL satisfies that hard requirement. The same paragraph recommends ignoring the `update_requested` flag in this condition. That implies the endpoint can complete the peer receive-key update, send no response KeyUpdate, and continue managing the connection until Section 5.5 limits require rekeying or closure.

wolfSSL instead propagates the shared send-limit error into the receive-response path. The implementation first sets pending response state, then calls the send function, then returns `BAD_STATE_E` when the send limit blocks the response. It does not convert this special case into success, and it does not clear `keyUpdateRespond`.

Classification:

- Hard Section 4.7.3 send-limit requirement: satisfied.
- Atomic "ignore `update_requested` at local limit" requirement: unsatisfied.
- Whole Section 4.7.3 limit-handling paragraph: partially satisfied.
- Normative strength: `SHOULD`-level deviation, not a hard `MUST` violation.

## Runtime Evidence

Fresh rerun date: 2026-08-10.

Action: a focused wolfSSL unit test used the in-tree `test_memio` framework to establish a complete two-end TLS 1.3 session. The test staged the local `keyUpdateCount` near the real boundary instead of executing about `2^48` KeyUpdates, but the KeyUpdate generation, encryption, transfer, parsing, receive-key switch, and error propagation all used real library code.

Two scenarios were run:

| Scenario | Initial local send count | Purpose |
|---|---:|---|
| Boundary reproducer | `2^48-1` | A response is not allowed; observe whether wolfSSL ignores the request and continues or returns an error. |
| Positive control | `2^48-2` | One response is allowed; verify the test path can drive a normal KeyUpdate response. |

Observed output:

```text
RFC9846_RERUN20260810_LIMIT peer_update=1 read=-1 error=-192 respond_flag=1 response_bytes=0 peer_app_write=1 second_read=-1 second_error=-192
RFC9846_RERUN20260810_CONTROL peer_update=1 read=-1 error=2 count_hi=65535 count_lo=4294967295 respond_flag=0 response_bytes=27
Failed/Skipped/Passed/All: 0/0/1/1
unit_test: Success for all configured tests.
```

Interpretation:

| Observation | Boundary reproducer | Positive control |
|---|---:|---:|
| Peer KeyUpdate send result | `1` success | `1` success |
| Client read result | `-1` | `-1` |
| `wolfSSL_get_error()` | `-192` (`BAD_STATE_E`) | `2` (`WOLFSSL_ERROR_WANT_READ`) |
| Response flag | `1` retained | `0` cleared |
| Response bytes produced | `0` | `27` |
| Later read after peer app data | `BAD_STATE_E` again | normal waiting state |

The boundary reproducer confirms the current behavior is "no over-limit response is sent, but the connection enters persistent `BAD_STATE_E`", not "the request is ignored and the connection continues". The positive control proves the same test path can drive a valid KeyUpdate response when the local counter is one below the limit.

Cleanup check: after the rerun, the temporary test registration was removed and `unit_test` rebuilt. The ordinary `test_tls13_KeyUpdate_sender_limit` still passed, the temporary rerun test name was no longer registered, and the temporary test name was absent from the edited source and header files.

## Impact

The trigger requires the local endpoint to have already sent `2^48-1` KeyUpdates, which is extremely unlikely in normal deployments. It is therefore not a practical routine remote denial-of-service path.

If the state is reached, a valid peer `KeyUpdate(update_requested)` causes the tested default build to return `BAD_STATE_E` to the application. The runtime test also confirmed that even after the peer sends decryptable application data, the local endpoint's next read returns the same error, so the connection cannot continue in the RFC-recommended way.

## Fix Direction

When processing `update_requested`, check the local send count before setting response state or calling `SendTls13KeyUpdate()`:

1. If the count is below `2^48-1`, keep the existing response path.
2. If the count is already at the limit, complete the peer receive-key update but do not set, or clear, `keyUpdateRespond`.
3. Return success for this special case and emit no KeyUpdate response record.
4. Continue monitoring the current sending key's AEAD usage according to RFC 9846 Section 5.5 and close only when the applicable usage limit requires it.

Add permanent regression tests for limit-minus-one, exact-limit, and subsequent application-data receive behavior.
