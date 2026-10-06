# quietShutdown can suppress the close_notify required after user_canceled

## Summary

The default `quietShutdown=0` path is compliant in the tested build. The issue is the quiet-on API combination: wolfSSL sends `user_canceled`, then `wolfSSL_shutdown()` returns success from the quiet-shutdown branch before the normal `close_notify` send path is reached.

## Standard Requirement

Official standard link:

- [RFC 9846 Section 6.1, Closure Alerts](https://datatracker.ietf.org/doc/html/rfc9846#section-6.1)

Normative text for `user_canceled`:

```text
user_canceled:  This alert notifies the recipient that the sender is
   canceling the handshake for some reason unrelated to a protocol
   failure.  If a user cancels an operation after the handshake is
   complete, just closing the connection by sending a "close_notify"
   is more appropriate.  This alert MUST be followed by a
   "close_notify".  This alert generally has AlertLevel=warning.
   Receiving implementations SHOULD continue to read data from the
   peer until a "close_notify" is received, though they MAY log or
   otherwise record them.
```

Requirement mapping:

- Condition: a TLS 1.3 endpoint sends `user_canceled`.
- Required behavior: the same endpoint subsequently sends `close_notify`.
- Non-compliant behavior: the endpoint ends the alert sequence after `user_canceled` without sending `close_notify`.

The nearby closure rule in RFC 9846 Section 6.1 allows skipping `close_notify` only after sending an error alert. `user_canceled` is defined in the closure-alert section, not the error-alert section, and `quietShutdown` is a local API option rather than a protocol-level error alert.

## Relevant Source Code

`src/ssl_api_rw.c:583-600` sends `user_canceled` and then calls `wolfSSL_shutdown()` on the same object:

```c
int wolfSSL_SendUserCanceled(WOLFSSL* ssl)
{
    int ret = WC_NO_ERR_TRACE(WOLFSSL_FAILURE);
    WOLFSSL_ENTER("wolfSSL_recv");

    if (ssl != NULL) {
        ssl->error = SendAlert(ssl, alert_warning, user_canceled);
        if (ssl->error < 0) {
            WOLFSSL_ERROR(ssl->error);
        }
        else {
            ret = wolfSSL_shutdown(ssl);
        }
    }

    WOLFSSL_LEAVE("wolfSSL_SendUserCanceled", ret);

    return ret;
}
```

`src/ssl_api_rw.c:613-616` handles quiet shutdown before the normal close-notify logic:

```c
if (ssl->options.quietShutdown) {
    WOLFSSL_MSG("quiet shutdown, no close notify sent");
    ret = WOLFSSL_SUCCESS;
}
```

`src/ssl_api_rw.c:650-658` is the normal branch that sends `close_notify` when quiet shutdown is not active:

```c
/* try to send close notify, not an error if can't */
if (!ssl->options.isClosed && !ssl->options.connReset &&
                              !ssl->options.sentNotify) {
    ssl->error = SendAlert(ssl, alert_warning, close_notify);

    /* the alert is now sent or sitting in the buffer,
     * where will be sent eventually */
    if (ssl->error == 0 || ssl->error == WC_NO_ERR_TRACE(WANT_WRITE))
        ssl->options.sentNotify = 1;
```

`src/ssl.c:4201-4216` exposes the quiet-shutdown setters in supported extra/compatibility builds:

```c
#if defined(OPENSSL_EXTRA) || defined(OPENSSL_EXTRA_X509_SMALL) || \
    defined(WOLFSSL_EXTRA) || defined(WOLFSSL_WPAS_SMALL)
    void wolfSSL_CTX_set_quiet_shutdown(WOLFSSL_CTX* ctx, int mode)
    {
        WOLFSSL_ENTER("wolfSSL_CTX_set_quiet_shutdown");
        if (mode)
            ctx->quietShutdown = 1;
    }


    void wolfSSL_set_quiet_shutdown(WOLFSSL* ssl, int mode)
    {
        WOLFSSL_ENTER("wolfSSL_set_quiet_shutdown");
        if (mode)
            ssl->options.quietShutdown = 1;
    }
#endif
```

The tested build exposes this path: `build-ca-names/wolfssl/options.h:225` defines `OPENSSL_EXTRA`, `build-ca-names/wolfssl/options.h:373` defines `WOLFSSL_TLS13`, and `build-ca-names/wolfssl/options.h:656-657` confirms TLS is not disabled.

## Implementation Behavior

When `quietShutdown` is disabled, `wolfSSL_SendUserCanceled()` sends `user_canceled`, then `wolfSSL_shutdown()` reaches the normal close-notify path and sends `close_notify`.

When `quietShutdown` is enabled, `wolfSSL_SendUserCanceled()` still sends `user_canceled`, but `wolfSSL_shutdown()` immediately returns `WOLFSSL_SUCCESS` from the quiet-shutdown branch. The follow-up `close_notify` required by RFC 9846 Section 6.1 is not sent.

This is not a general objection to quiet shutdown. The inconsistency appears because wolfSSL first emits the standard-triggering `user_canceled` alert, then lets a local quiet-shutdown option suppress the mandatory follow-up alert.

## Inconsistency Reason

RFC 9846 requires `user_canceled` to be followed by `close_notify`. wolfSSL's quiet-on path sends `user_canceled`, enters `wolfSSL_shutdown()`, returns success from the quiet branch, and skips the `close_notify` send path.

The closure-alert exception does not apply. The standard permits omitting `close_notify` only after an error alert has already been sent; `user_canceled` is a closure alert, and `quietShutdown` is not an alert sent on the wire.

Because the path is reachable through public API in the tested supported build, the configuration dependence limits the scope but does not make the finding a false positive.

## Runtime Evidence

Fresh rerun date: 2026-08-10.

Action: the focused runtime check was run from the workspace root against the wolfSSL debug build exposing quiet-shutdown APIs. The check compared two TLS 1.3 cases on the same API path: quiet shutdown disabled as a positive control, and quiet shutdown enabled as the reproducer.

Run result:

- Process exit code: `0`
- stderr length: `0`
- stdout SHA-256: `60C50B469252EBAE81FB28EB1933294256D2CA518B4D8BB6466830842C40E639`
- Reported result: `runtime_reproduction_confirmed: true`

Observed alert behavior:

| Case | quietShutdown | API return | Wire hex | Parsed alerts | Peer result |
|---|---:|---:|---|---|---|
| positive control | `false` | `2` (`WOLFSSL_SHUTDOWN_NOT_DONE`) | `1503030002015a15030300020100` | `warning/user_canceled(90) -> warning/close_notify(0)` | `WOLFSSL_ERROR_ZERO_RETURN(6)`, last_rx `close_notify(0)` |
| reproducer | `true` | `1` (`WOLFSSL_SUCCESS`) | `1503030002015a` | `warning/user_canceled(90)` only | `WOLFSSL_ERROR_WANT_READ(2)`, last_rx `user_canceled(90)` |

Interpretation: with quiet shutdown disabled, wolfSSL sends `user_canceled` followed by `close_notify`, and the peer observes clean closure. With quiet shutdown enabled, wolfSSL sends only `user_canceled`; `wolfSSL_shutdown()` returns local success before `close_notify` is sent, and the peer continues waiting for closure.

Earlier corrected runs are consistent with the fresh rerun: they exited with code `0` and produced identical normalized stdout hashes. They are not needed as external evidence because the decisive observations are included above.

Invalid early attempts are excluded because certificates were not loaded or the CA input format prevented SSL objects from being created. Those attempts did not reach the target `wolfSSL_SendUserCanceled()` behavior.

## Impact

Peers that follow RFC 9846's receive-side guidance may continue waiting for `close_notify` after receiving `user_canceled`, producing shutdown stalls, timeouts, or delayed resource release.

The local `WOLFSSL_SUCCESS` return can also make applications believe the cancellation and closure sequence completed cleanly when the required follow-up alert was not sent.

## Fix Direction

`wolfSSL_SendUserCanceled()` should make the required follow-up `close_notify` non-suppressible by `quietShutdown`. A focused fix can temporarily bypass the quiet branch for this API path, enqueue or send `close_notify`, and preserve existing nonblocking `WANT_WRITE` retry semantics.

If wolfSSL instead wants to forbid this API combination, it should reject the call before sending `user_canceled`; it should not emit `user_canceled` and then finish without `close_notify`.

Add a TLS 1.3 regression test covering both quiet-off and quiet-on cases, asserting the raw alert sequence and peer-observed `last_rx`.

## Remaining Uncertainty

No remaining uncertainty affects the classification. Other protocol versions and unrelated quiet-shutdown call sequences were not evaluated and are outside this root-cause report.
