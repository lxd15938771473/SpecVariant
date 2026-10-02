# OpenSSL issue: user_canceled is not followed by close_notify

## Summary

OpenSSL exposes documented callback paths where application code can choose `SSL_AD_USER_CANCELLED` as the alert description. Runtime testing against the local OpenSSL `4.1.0-dev` build shows that those paths send a TLS 1.3 `user_canceled` alert without sending a following `close_notify`.

## Standard Requirement

Primary reference: RFC 8446, Section 6.1, Closure Alerts, [official text](https://www.rfc-editor.org/rfc/rfc8446.html#section-6.1)

Current TLS 1.3 reference: RFC 9846, Section 6.1, Closure Alerts, [official text](https://www.rfc-editor.org/rfc/rfc9846.html#section-6.1). RFC 9846 was published in July 2026 and obsoletes RFC 8446.

RFC 8446 says:

```text
user_canceled:  This alert notifies the recipient that the sender is
      canceling the handshake for some reason unrelated to a protocol
      failure.  If a user cancels an operation after the handshake is
      complete, just closing the connection by sending a "close_notify"
      is more appropriate.  This alert SHOULD be followed by a
      "close_notify".  This alert generally has AlertLevel=warning.
```

Interpretation:

Under RFC 8446 this is a `SHOULD`, not a `MUST`. RFC 2119 still treats `SHOULD` as normative unless an implementation has a specific, understood, and justified reason to deviate. RFC 9846 strengthens the same requirement to `MUST`: a `user_canceled` alert must be followed by `close_notify`.

TLS 1.3 also states that alert severity is implicit and the `level` field can safely be ignored. Therefore the runtime records with `level=2 desc=90` are still `user_canceled` alerts for this requirement.

## Relevant Source Code

### Generic alert send path

`ssl/s3_msg.c:45`

```c
int ssl3_send_alert(SSL_CONNECTION *s, int level, int desc)
{
    SSL *ssl = SSL_CONNECTION_GET_SSL(s);

    if (SSL_CONNECTION_TREAT_AS_TLS13(s))
        desc = tls13_alert_code(desc);
    else
        desc = ssl->method->ssl3_enc->alert_value(desc);
    if (desc < 0)
        return -1;
    if (s->shutdown & SSL_SENT_SHUTDOWN && desc != SSL_AD_CLOSE_NOTIFY)
        return -1;

    s->s3.alert_dispatch = SSL_ALERT_DISPATCH_PENDING;
    s->s3.send_alert[0] = level;
    s->s3.send_alert[1] = desc;
    if (!RECORD_LAYER_write_pending(&s->rlayer))
        return ssl->method->ssl_dispatch_alert(ssl);
    return -1;
}
```

This function queues exactly the requested alert. It maps `SSL_AD_USER_CANCELLED` to the TLS wire value, but it does not enqueue an additional `close_notify`.

### close_notify send path

`ssl/s3_lib.c:5079`

```c
if (!(sc->shutdown & SSL_SENT_SHUTDOWN)) {
    sc->shutdown |= SSL_SENT_SHUTDOWN;
    ssl3_send_alert(sc, SSL3_AL_WARNING, SSL_AD_CLOSE_NOTIFY);
    if (sc->s3.alert_dispatch > 0)
        return -1;
}
```

`close_notify` is sent by the clean shutdown path. This path is separate from the callback-triggered alert paths below.

### ClientHello callback path

`doc/man3/SSL_CTX_set_client_hello_cb.pod:33`

The manpage documents that a failing ClientHello callback terminates the connection and supplies the outbound alert value through the `al` parameter.

`ssl/statem/statem_srvr.c:1902`

```c
if (sctx->client_hello_cb != NULL) {
    switch (sctx->client_hello_cb(ussl, &al, sctx->client_hello_cb_arg)) {
    case SSL_CLIENT_HELLO_SUCCESS:
        break;
    case SSL_CLIENT_HELLO_RETRY:
        s->rwstate = SSL_CLIENT_HELLO_CB;
        return -1;
    case SSL_CLIENT_HELLO_ERROR:
    default:
        SSLfatal(s, al, SSL_R_CALLBACK_FAILED);
        goto err;
    }
}
```

An application can set `*al = SSL_AD_USER_CANCELLED` and return `SSL_CLIENT_HELLO_ERROR`. OpenSSL then passes `SSL_AD_USER_CANCELLED` to `SSLfatal()`.

### SNI callback path

`doc/man3/SSL_CTX_set_tlsext_servername_callback.pod:48`

The SNI callback manpage documents `SSL_TLSEXT_ERR_ALERT_FATAL` and says the callback stores the alert value in `al`.

`ssl/statem/extensions.c:1384`

```c
if (sctx->ext.servername_cb != NULL)
    ret = sctx->ext.servername_cb(ussl, &altmp,
        sctx->ext.servername_arg);
else if (s->session_ctx->ext.servername_cb != NULL)
    ret = s->session_ctx->ext.servername_cb(ussl, &altmp,
        s->session_ctx->ext.servername_arg);

switch (ret) {
case SSL_TLSEXT_ERR_ALERT_FATAL:
    SSLfatal(s, altmp, SSL_R_CALLBACK_FAILED);
    return 0;
```

This gives a second public path where application code can select `SSL_AD_USER_CANCELLED` and cause unmodified OpenSSL to send it.

## Implementation Behavior

OpenSSL can send `user_canceled` through public callbacks, but the alert send path emits only the selected alert. The clean shutdown path independently emits `close_notify`, and no inspected callback-triggered path chains from `user_canceled` to `ssl3_shutdown()` or otherwise queues a second alert.

The behavior is therefore:

- Normal TLS 1.3 shutdown: sends `close_notify`.
- Callback-selected `SSL_AD_USER_CANCELLED`: sends `user_canceled`.
- Callback-selected `SSL_AD_USER_CANCELLED`: does not send a following `close_notify`.

## Inconsistency Reason

RFC 8446 says `user_canceled` should be followed by `close_notify`; RFC 9846 strengthens this to must be followed by `close_notify`. OpenSSL exposes public callback paths where `SSL_AD_USER_CANCELLED` can be selected, but those paths call `SSLfatal()` and `ssl3_send_alert()` for only the selected alert. Because the `close_notify` logic remains isolated in `ssl3_shutdown()`, the required follow-up alert is not sent.

The issue is callback-triggered rather than a hard-coded OpenSSL cancellation branch. It is still a real implementation behavior because the callback APIs are documented public APIs and the reproducers use unmodified OpenSSL.

## Runtime Evidence

The test linked a small in-memory client/server probe against OpenSSL `4.1.0-dev`, restricted both peers to TLS 1.3, and recorded outbound alerts with `SSL_set_msg_callback()`. It exercised normal shutdown as a positive control and then selected `SSL_AD_USER_CANCELLED` independently through the documented ClientHello and SNI callbacks.

### Positive control: normal TLS 1.3 shutdown

```text
negotiated version: TLSv1.3 / TLSv1.3
client write alert: level=1 desc=0 (close_notify)
server write alert: level=1 desc=0 (close_notify)
client writes: total_alerts=1 close_notify=1 user_canceled=0
server writes: total_alerts=1 close_notify=1 user_canceled=0
```

The control confirms that the clean `close_notify` path is live.

### Reproducer 1: ClientHello callback selects user_canceled

```text
server client_hello_cb: returning SSL_CLIENT_HELLO_ERROR with al=90 (user_canceled)
server write alert: level=2 desc=90 (user_canceled)
client writes: total_alerts=0 close_notify=0 user_canceled=0
server writes: total_alerts=1 close_notify=0 user_canceled=1
```

The server sends exactly one `user_canceled` alert and no `close_notify`.

### Reproducer 2: SNI callback selects user_canceled

```text
server sni_cb: name=cancel.example, returning SSL_TLSEXT_ERR_ALERT_FATAL with al=90 (user_canceled)
server write alert: level=2 desc=90 (user_canceled)
client writes: total_alerts=0 close_notify=0 user_canceled=0
server writes: total_alerts=1 close_notify=0 user_canceled=1
```

The second public callback path independently confirms the same behavior.

## Impact

Applications that select `SSL_AD_USER_CANCELLED` through documented callbacks can produce a TLS 1.3 `user_canceled` alert stream that is not followed by `close_notify`. Under RFC 8446 this violates a normative `SHOULD` unless OpenSSL has a specific justified reason for omitting the follow-up. Under RFC 9846 this is a direct `MUST` violation.

The practical scope depends on applications choosing `SSL_AD_USER_CANCELLED` as the callback alert value. Normal `SSL_shutdown()` behavior is not affected.

## Fix Direction

When OpenSSL sends `SSL_AD_USER_CANCELLED` in TLS 1.3, it should queue or otherwise send a following `close_notify` unless the connection state makes doing so impossible. The fix should be placed close to the alert-send or fatal-callback path so all public callback routes are covered, while preserving the existing clean shutdown behavior and nonblocking alert retry semantics.
