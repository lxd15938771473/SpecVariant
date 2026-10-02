# Server post-Finished records remain on handshake keys

## Summary

This recheck confirms a real TLS 1.3 compliance defect, not just a static
state-machine suspicion.

After the server sends `Finished`, Mbed TLS derives the application traffic
transform but does not install it as the active outbound transform. The server
then waits for the client second flight while `transform_out` still points to
the handshake epoch. If client authentication fails on that path, the pending
fatal alert is sent immediately under handshake keys even though RFC 8446
requires every post-`Finished` record to use application traffic keys.

A focused cross-stack reproducer confirmed the on-wire effect: the peer
completed the initial TLS 1.3 handshake, then failed to decrypt the server's
post-`Finished` alert with `DECRYPTION_FAILED_OR_BAD_RECORD_MAC`.

## Standard Requirement

- Official standard links:
  - [RFC 8446 Section 4.4.4 Finished](https://www.rfc-editor.org/rfc/rfc8446#section-4.4.4)
  - [RFC 8446 Section 4.4.2.4 Receiving a Certificate Message](https://www.rfc-editor.org/rfc/rfc8446#section-4.4.2.4)
  - [RFC 8446 Appendix A.2 Server](https://www.rfc-editor.org/rfc/rfc8446#appendix-A.2)

Relevant normative text from Section 4.4.4:

```text
Any records following a Finished message MUST be encrypted under the
appropriate application traffic key as described in Section 7.2. In
particular, this includes any alerts sent by the server in response
to client Certificate and CertificateVerify messages.
```

Relevant text from Section 4.4.2.4:

```text
If the client does not send any certificates (i.e., it sends an empty
Certificate message), the server MAY at its discretion either
continue the handshake without client authentication or abort the
handshake with a "certificate_required" alert.
```

Supporting state-machine text from Appendix A.2:

```text
app data                       | Send Finished
 after   -->                   | K_send = application
 here                  +--------+--------+
```

Explanation:

This is not only an Appendix A.2 interpretation issue. Section 4.4.4 gives a
direct record-layer `MUST`: once the server has sent `Finished`, any later
record, including a certificate-related alert emitted while processing the
client's second flight, must be encrypted under application traffic keys.
Appendix A.2 matches that requirement by showing `K_send = application`
immediately after `Send Finished`.

## Relevant Source Code

### `library/ssl_tls13_server.c:2874-2910`

`ssl_tls13_write_server_finished()` writes the server `Finished` message and
derives `transform_application`, but it does not install that transform for
outbound traffic:

```c
static int ssl_tls13_write_server_finished(mbedtls_ssl_context *ssl)
{
    int ret = MBEDTLS_ERR_ERROR_CORRUPTION_DETECTED;

    ret = mbedtls_ssl_tls13_write_finished_message(ssl);
    if (ret != 0) {
        return ret;
    }

    ret = mbedtls_ssl_tls13_compute_application_transform(ssl);
    if (ret != 0) {
        MBEDTLS_SSL_PEND_FATAL_ALERT(
            MBEDTLS_SSL_ALERT_MSG_HANDSHAKE_FAILURE,
            MBEDTLS_ERR_SSL_HANDSHAKE_FAILURE);
        return ret;
    }

    MBEDTLS_SSL_DEBUG_MSG(
        1, ("Switch to handshake keys for inbound traffic "
            "( K_recv = handshake )"));
    mbedtls_ssl_set_inbound_transform(ssl, ssl->handshake->transform_handshake);

    ssl_tls13_prepare_for_handshake_second_flight(ssl);

    return 0;
}
```

The function computes application keys but leaves the active outbound transform
unchanged.

### `library/ssl_tls13_server.c:2857-2866` and `3545-3564`

After server `Finished`, the handshake advances into the client second-flight
states, not directly into wrapup:

```c
static void ssl_tls13_prepare_for_handshake_second_flight(
    mbedtls_ssl_context *ssl)
{
    if (ssl->handshake->certificate_request_sent) {
        mbedtls_ssl_handshake_set_state(ssl, MBEDTLS_SSL_CLIENT_CERTIFICATE);
    } else {
        mbedtls_ssl_handshake_set_state(ssl, MBEDTLS_SSL_CLIENT_FINISHED);
    }
}
```

```c
case MBEDTLS_SSL_CLIENT_CERTIFICATE:
    ret = mbedtls_ssl_tls13_process_certificate(ssl);
    if (ret == 0) {
        if (ssl->session_negotiate->peer_cert != NULL) {
            mbedtls_ssl_handshake_set_state(
                ssl, MBEDTLS_SSL_CLIENT_CERTIFICATE_VERIFY);
        } else {
            mbedtls_ssl_handshake_set_state(
                ssl, MBEDTLS_SSL_CLIENT_FINISHED);
        }
    }
    break;
```

So there is a real post-`Finished` window in which the server may still need to
emit alerts while not yet having reached handshake wrapup.

### `library/ssl_tls13_generic.c:675-691` and `724-741`

An empty client certificate chain under required authentication sets a pending
fatal alert during that post-`Finished` window:

```c
if (ssl->session_negotiate->peer_cert == NULL) {
    MBEDTLS_SSL_DEBUG_MSG(1, ("peer has no certificate"));

    if (ssl->conf->endpoint == MBEDTLS_SSL_IS_SERVER) {
        ssl->session_negotiate->verify_result = MBEDTLS_X509_BADCERT_MISSING;
        if (authmode == MBEDTLS_SSL_VERIFY_OPTIONAL) {
            return 0;
        } else {
            MBEDTLS_SSL_PEND_FATAL_ALERT(
                MBEDTLS_SSL_ALERT_MSG_NO_CERT,
                MBEDTLS_ERR_SSL_NO_CLIENT_CERTIFICATE);
            return MBEDTLS_ERR_SSL_NO_CLIENT_CERTIFICATE;
        }
    }
}
```

```c
int mbedtls_ssl_tls13_process_certificate(mbedtls_ssl_context *ssl)
{
    ...
    MBEDTLS_SSL_PROC_CHK(mbedtls_ssl_tls13_parse_certificate(ssl, buf,
                                                             buf + buf_len));
    MBEDTLS_SSL_PROC_CHK(ssl_tls13_validate_certificate(ssl));
    ...
}
```

The parser and validator can therefore trigger a certificate-related alert
before client `Finished` has been processed.

### `library/ssl_tls.c:4262-4268`, `library/ssl_msg.c:6236-6247`, and `library/ssl_msg.c:5044-5068`

When a handshake step returns an error with `send_alert` set, the handshake
driver immediately sends the pending fatal alert with the current outbound
transform:

```c
if (ret != 0) {
    if (ssl->send_alert) {
        ret = mbedtls_ssl_handle_pending_alert(ssl);
        goto cleanup;
    }
}
```

```c
int mbedtls_ssl_handle_pending_alert(mbedtls_ssl_context *ssl)
{
    ...
    ret = mbedtls_ssl_send_alert_message(ssl,
                                         MBEDTLS_SSL_ALERT_LEVEL_FATAL,
                                         ssl->alert_type);
    ...
}
```

```c
int mbedtls_ssl_send_alert_message(mbedtls_ssl_context *ssl,
                                   unsigned char level,
                                   unsigned char message)
{
    ...
    ssl->out_msgtype = MBEDTLS_SSL_MSG_ALERT;
    ssl->out_msglen = 2;
    ssl->out_msg[0] = level;
    ssl->out_msg[1] = message;

    if ((ret = mbedtls_ssl_write_record(ssl, SSL_FORCE_FLUSH)) != 0) {
        return ret;
    }
    ...
}
```

There is no special late switch here. The alert is emitted with whatever
`transform_out` is currently active.

### `library/ssl_tls13_generic.c:1250-1259` and `library/ssl_tls13_server.c:3067-3110`

The outbound application transform is not installed until handshake wrapup,
which happens only after successful processing of client `Finished`:

```c
void mbedtls_ssl_tls13_handshake_wrapup(mbedtls_ssl_context *ssl)
{
    MBEDTLS_SSL_DEBUG_MSG(1, ("Switch to application keys for inbound traffic"));
    mbedtls_ssl_set_inbound_transform(ssl, ssl->transform_application);

    MBEDTLS_SSL_DEBUG_MSG(1, ("Switch to application keys for outbound traffic"));
    mbedtls_ssl_set_outbound_transform(ssl, ssl->transform_application);
}
```

```c
static int ssl_tls13_process_client_finished(mbedtls_ssl_context *ssl)
{
    ...
    mbedtls_ssl_handshake_set_state(ssl, MBEDTLS_SSL_HANDSHAKE_WRAPUP);
    return 0;
}
```

This is too late for alerts that must be sent while processing the client
certificate or certificate-verify messages.

## Implementation Behavior

The effective server-side control flow is:

1. Before the encrypted server flight, outbound traffic is switched to the
   handshake transform in `ssl_tls13_write_encrypted_extensions()`.
2. `ssl_tls13_write_server_finished()` sends `Finished` and derives
   `transform_application`, but it does not call
   `mbedtls_ssl_set_outbound_transform(ssl, ssl->transform_application)`.
3. The server then enters `MBEDTLS_SSL_CLIENT_CERTIFICATE`,
   `MBEDTLS_SSL_CLIENT_CERTIFICATE_VERIFY`, or `MBEDTLS_SSL_CLIENT_FINISHED`
   depending on the handshake mode.
4. Certificate parse or validation failures in those states set a pending fatal
   alert.
5. `mbedtls_ssl_handshake_step()` immediately flushes that alert through
   `mbedtls_ssl_send_alert_message()`, still using the current handshake
   outbound transform.
6. Only the success path through client `Finished` reaches
   `mbedtls_ssl_tls13_handshake_wrapup()`, where outbound traffic finally
   switches to `ssl->transform_application`.

No earlier server-side call was found on this path that installs the
application transform for outbound records.

## Inconsistency Reason

RFC 8446 requires every record after server `Finished` to use application
traffic keys, and it explicitly names the exact class of records exercised
here: alerts sent by the server in response to client `Certificate` or
`CertificateVerify` messages.

Mbed TLS instead keeps `transform_out` on handshake keys until handshake
wrapup after client `Finished`. Therefore, if client authentication fails in
the middle of the client second flight, the implementation emits a real
post-`Finished` record under the wrong key phase.

This is stronger than a mere "the API does not let the server send application
data early" concern. The mismatch affects concrete on-wire records that the RFC
requires or permits on the server's error path.

The runtime logs also show that the chosen alert description is legacy value
`41` (`no_certificate_RESERVED` in TLS 1.3), which may itself be a separate
issue. However, even if that alert code were corrected, the record-layer key
phase mismatch proven here would remain.

## Runtime Evidence

### Round 1

- Status: `completed`
- Positive control: `run`
- Reproducer: `run`

The focused check ran the mbedTLS sample server on loopback with TLS 1.3 and
required client authentication. A Python standard-library TLS client, backed
by OpenSSL, connected without presenting a client certificate and attempted
one read after handshake completion.

The Python client uses the standard-library `ssl` module backed by OpenSSL,
forces TLS 1.3, does not present a client certificate, and attempts a single
post-handshake read after the TLS session becomes established.

Observed behavior:

- The peer completed the initial TLS 1.3 handshake, reporting `HANDSHAKE_OK`
  with cipher `TLS_AES_256_GCM_SHA384`.
- The server had already derived application write IV
  `58 6f ec 62 9a 65 67 57 4b 22 90 8b`.
- It then switched only inbound traffic to handshake keys and moved from
  `MBEDTLS_SSL_SERVER_FINISHED` to `MBEDTLS_SSL_CLIENT_CERTIFICATE`.
- After logging `peer has no certificate`, it sent fatal alert `41`.
- That alert used IV `46 84 a9 c4 c5 8b ec b0 fa fd 9a ba`, not the application
  write IV above.
- The peer's first post-handshake read failed with
  `DECRYPTION_FAILED_OR_BAD_RECORD_MAC`.

Runtime interpretation:

The peer completed the TLS 1.3 handshake and only failed when consuming the
server's post-`Finished` alert. That is exactly the failure mode expected when
the server emits a record after `Finished` under the wrong key phase. The
cross-stack reproducer therefore confirms that the code-path mismatch is not
just theoretical; it produces a real interoperability failure on the wire.

## Impact

This defect can break interoperability with compliant TLS 1.3 peers whenever
the server needs to reject client authentication after sending its own
`Finished`. Instead of receiving a usable TLS 1.3 alert, the peer receives an
undecryptable post-`Finished` record and surfaces a generic bad-record-MAC
failure.

More broadly, the delayed outbound switch leaves any server-side record emitted
between server `Finished` and handshake wrapup exposed to the same key-phase
error.

## Fix Direction

1. Install `ssl->transform_application` as the outbound transform immediately
   after successfully sending server `Finished`.
2. Keep the current inbound transition logic separate. RFC 8446 Appendix A.2 is
   asymmetric here: after server `Finished`, `K_send` is already application
   while `K_recv` still depends on the client second-flight state.
3. Add a regression test in which a TLS 1.3 server with required client
   authentication receives an empty client `Certificate`, and a cross-stack
   peer must receive a decryptable post-`Finished` alert.
4. Add a second negative test for failure while processing client
   `CertificateVerify`, which is also explicitly covered by Section 4.4.4.
5. Track the separate alert-description choice (`certificate_required` vs
   legacy `41`) independently so that the key-phase fix is not conflated with
   alert-code cleanup.

## Consolidation Record

This canonical report also absorbs requirements `req-76ea1fc49c3f72a43b85` and `req-f0c4405d666b27060201`. Both exercised the same post-server-`Finished` window in which the application transform had been derived but was not installed before an alert record was sent.
