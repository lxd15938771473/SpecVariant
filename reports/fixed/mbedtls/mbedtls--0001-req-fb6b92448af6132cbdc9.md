# Stream TLS does not fragment oversized `Certificate` handshake messages across records

## Summary

This is a confirmed issue.

On the stream-TLS send path, mbedTLS builds each handshake message in a single output buffer and writes it as a single record. When the server `Certificate` message becomes larger than the available record payload, the implementation does not fragment it across multiple TLS records. Instead:

- on the TLS 1.2 path, the server returns `MBEDTLS_ERR_SSL_BUFFER_TOO_SMALL` with `certificate too large, 16767 > 16384`;
- on the TLS 1.3 path, the handshake fails in the server-certificate state for the same oversized-chain reproducer;
- with a negotiated 512-byte maximum record payload, the server still emits an 850-byte `Certificate` record instead of fragmenting it.

The issue is therefore real. It is primarily an interoperability and protocol-compliance problem on the outgoing handshake path, not just a static suspicion.

## Standard Requirement

Official standard: [RFC 8446](https://www.rfc-editor.org/rfc/rfc8446)

Section 4.4.2 "Certificate" requires the endpoint to send the certificate chain when certificate-based authentication is in use:

```text
This message conveys the endpoint's certificate chain to the peer.

The server MUST send a Certificate message whenever the agreed-upon
key exchange method uses certificates for authentication (this
includes all key exchange methods defined in this document
except PSK).
```

Source: [RFC 8446](https://www.rfc-editor.org/rfc/rfc8446.html)

Section 5.1 "Record Layer" allows handshake messages to be split across multiple TLS records:

```text
Handshake messages MAY be coalesced into a single TLSPlaintext record
or fragmented across several records, provided that:

-  Handshake messages MUST NOT be interleaved with other record
   types.

Implementations MUST NOT send zero-length fragments of Handshake
types, even if those fragments contain padding.
```

Source: [RFC 8446](https://www.rfc-editor.org/rfc/rfc8446.html)

Appendix C.3 explicitly calls out this implementation concern for large certificate-bearing handshake messages:

```text
Do you fragment handshake messages that exceed the
maximum fragment size?  In particular, the Certificate and
CertificateRequest handshake messages can be large enough to
require fragmentation.
```

Source: [RFC 8446](https://www.rfc-editor.org/rfc/rfc8446.html)

The key point is not that Appendix C.3 alone creates a new `MUST`; rather, Section 4.4.2 requires sending `Certificate`, and Section 5.1 defines record-layer fragmentation as the compliant way to carry oversized handshake messages. If an implementation can only send `Certificate` when it fits in a single record-sized buffer, then valid certificate chains can become unsendable.

## Relevant Source Code

Below, source paths are relative to the mbedTLS repository root.

`library/ssl_msg.c:2445-2456` allocates a single contiguous output buffer for a handshake message:

```c
int mbedtls_ssl_start_handshake_msg(mbedtls_ssl_context *ssl, unsigned char hs_type,
                                    unsigned char **buf, size_t *buf_len)
{
    /*
     * Reserve 4 bytes for handshake header. ( Section 4,RFC 8446 )
     */
    *buf = ssl->out_msg + 4;
    *buf_len = MBEDTLS_SSL_OUT_CONTENT_LEN - 4;

    ssl->out_msgtype = MBEDTLS_SSL_MSG_HANDSHAKE;
```

This API exposes one record-sized buffer, not a fragmenting writer.

`library/ssl_msg.c:2525-2531` and `library/ssl_msg.c:2598-2610` show that stream TLS does not add an outgoing handshake-fragmentation step, while DTLS has a dedicated flight path:

```c
    /*
     * This should never fail as the various message
     * writing functions must obey the bounds of the
     * outgoing record buffer, but better be safe.
     *
     * Note: We deliberately do not check for the MTU or MFL here.
     */
    if (ssl->out_msglen > MBEDTLS_SSL_OUT_CONTENT_LEN) {
```

```c
#if defined(MBEDTLS_SSL_PROTO_DTLS)
    if (ssl->conf->transport == MBEDTLS_SSL_TRANSPORT_DATAGRAM &&
        !(ssl->out_msgtype == MBEDTLS_SSL_MSG_HANDSHAKE &&
          hs_type          == MBEDTLS_SSL_HS_HELLO_REQUEST)) {
        if ((ret = ssl_flight_append(ssl)) != 0) {
            return ret;
        }
    } else
#endif
    {
        if ((ret = mbedtls_ssl_write_record(ssl, force_flush)) != 0) {
```

The DTLS branch has special handling for handshake flights and fragments; the stream-TLS branch falls through to a single `mbedtls_ssl_write_record()` call.

`library/ssl_tls.c:6745-6751` rejects an oversized TLS 1.2 certificate chain instead of fragmenting it:

```c
    while (crt != NULL) {
        n = crt->raw.len;
        if (n > MBEDTLS_SSL_OUT_CONTENT_LEN - 3 - i) {
            MBEDTLS_SSL_DEBUG_MSG(1, ("certificate too large, %" MBEDTLS_PRINTF_SIZET
                                      " > %" MBEDTLS_PRINTF_SIZET,
                                      i + 3 + n, (size_t) MBEDTLS_SSL_OUT_CONTENT_LEN));
            return MBEDTLS_ERR_SSL_BUFFER_TOO_SMALL;
        }
```

`library/ssl_tls13_generic.c:850-858` builds the TLS 1.3 `Certificate` message body in the same single-buffer model:

```c
    MBEDTLS_SSL_DEBUG_MSG(2, ("=> write certificate"));

    MBEDTLS_SSL_PROC_CHK(mbedtls_ssl_start_handshake_msg(
                             ssl, MBEDTLS_SSL_HS_CERTIFICATE, &buf, &buf_len));

    MBEDTLS_SSL_PROC_CHK(ssl_tls13_write_certificate_body(ssl,
                                                          buf,
                                                          buf + buf_len,
                                                          &msg_len));
```

`include/mbedtls/ssl.h:4359-4361` also documents the current limitation directly:

```c
 * \note           With TLS, this currently only affects ApplicationData (sent
 *                 with \c mbedtls_ssl_read()), not handshake messages.
 *                 With DTLS, this affects both ApplicationData and handshake.
```

That note is not the root proof by itself, but it is consistent with the runtime behavior below.

## Implementation Behavior

The implementation supports incoming TLS handshake reassembly and DTLS-specific fragment handling, but it does not implement outgoing handshake fragmentation for stream TLS.

Concretely:

- the generic handshake sender gives each handshake writer one buffer of size `MBEDTLS_SSL_OUT_CONTENT_LEN - 4`;
- the stream-TLS send path writes `ssl->out_msglen` as one record;
- the TLS 1.2 certificate writer aborts when the encoded chain no longer fits in that single record-sized buffer;
- the TLS 1.3 certificate writer uses the same single-buffer construction model and fails for the oversized-chain reproducer in the server-certificate state.

So the effective behavior is "send the entire handshake message in one TLS record or fail", not "fragment across several records when needed".

## Inconsistency Reason

RFC 8446 requires a certificate-authenticated endpoint to send `Certificate`, and it allows handshake messages to be fragmented across multiple TLS records when necessary. mbedTLS's stream-TLS send path does not provide that capability for oversized handshake messages. Instead, it assumes that each handshake message must fit into one record-sized output buffer.

That creates two observable mismatches:

1. A certificate chain that is valid but too large for one record cannot be sent at all on the TLS 1.2 path; the library aborts with `MBEDTLS_ERR_SSL_BUFFER_TOO_SMALL` instead of fragmenting.
2. When a smaller record payload is negotiated, the library still sends an oversized handshake record rather than splitting the `Certificate` message across multiple records.

Appendix C.3 explicitly warns implementers about this exact corner case for `Certificate` and `CertificateRequest`, which matches the failing behavior here.

## Runtime Evidence

The mbedTLS example endpoints `ssl_server2` and `ssl_client2` were run on
loopback.

### 1. Control case succeeds

The ordinary TLS 1.2 control handshake succeeded with the sample certificate,
a 16384-byte payload limit, and normal application data after handshake
completion:

Relevant log lines:

```text
[ Protocol is TLSv1.2 ]
[ Maximum incoming record payload length is 16384 ]
[ Maximum outgoing record payload length is 16384 ]
> Write to client: 156 bytes written in 1 fragments
```

This confirms that the test setup itself is healthy.

### 2. Oversized TLS 1.2 `Certificate` fails instead of fragmenting

An oversized synthetic chain was created to push the encoded `Certificate` message above one record payload. The failure happens on the server while writing its own certificate message, before any peer-side certificate validation question matters.


Relevant log lines:

```text
ssl_tls.c:6704: |2| => write certificate
ssl_tls.c:6748: |1| certificate too large, 16767 > 16384
! mbedtls_ssl_handshake returned -0x8a
```

```text
! mbedtls_ssl_handshake returned -0x7280
```

This is direct runtime proof that the stream-TLS sender does not fragment an oversized `Certificate` message across records.

### 3. Negotiated 512-byte record payload still carries one 850-byte `Certificate`

A second reproducer negotiated a 512-byte maximum record payload. Even then, the server wrote an 850-byte `Certificate` message as one record-sized send operation.


Relevant log lines:

```text
ssl_msg.c:2077: |2| message length: 850, out_left: 850
[ Maximum incoming record payload length is 512 ]
[ Maximum outgoing record payload length is 512 ]
```

```text
ssl_msg.c:2005: |2| in_left: 5, nb_want: 850
ssl_msg.c:2025: |2| in_left: 5, nb_want: 850
[ Maximum incoming record payload length is 512 ]
[ Maximum outgoing record payload length is 512 ]
```

This shows the same structural limitation from another angle: once handshake messages are not fragmentable on the stream-TLS send path, negotiated small payload sizes do not constrain outgoing handshake records correctly.

### 4. TLS 1.3 shows the same failure mode

The TLS 1.3 control also succeeded, ruling out a generic TLS 1.3 setup problem.
The oversized-chain run then failed while the server was in
`MBEDTLS_SSL_SERVER_CERTIFICATE`:

```text
[ Protocol is TLSv1.3 ]
[ Maximum incoming record payload length is 16384 ]
[ Maximum outgoing record payload length is 16383 ]
```

```text
ssl_tls13_generic.c:0850: |2| => write certificate
ssl_tls13_generic.c:0867: |2| <= write certificate
! mbedtls_ssl_handshake returned -0x8a
```

```text
! mbedtls_ssl_handshake returned -0x7280
```

So the limitation is not confined to the TLS 1.2 certificate writer; the TLS 1.3 stream path exhibits the same issue for oversized certificate output.

## Impact

The impact is practical:

- servers can fail handshakes with large but otherwise usable certificate chains;
- stream-TLS handshakes do not correctly adapt when the effective outgoing record payload is smaller than the encoded `Certificate` message;
- interoperability suffers specifically in certificate-heavy deployments and constrained-record configurations.

This is best classified as a confirmed protocol-compliance and interoperability defect on the outgoing stream-TLS handshake path.

## Fix Direction

The robust fix is to add outgoing handshake fragmentation for stream TLS, analogous in spirit to the existing DTLS-specific fragment-aware handling, so that `Certificate` and other large handshake messages can span multiple records while preserving the ordering constraints from RFC 8446 Section 5.1.

At minimum, the implementation should avoid silently depending on a single-record handshake model:

- the TLS 1.2 and TLS 1.3 certificate-writing paths should not assume the full encoded chain fits in one record-sized buffer;
- if small record limits are negotiated, outgoing handshake messages must either honor those limits by fragmenting or reject the configuration/negotiation consistently before handshake progress depends on unsupported behavior;
- tests should be added for oversized `Certificate` and `CertificateRequest` messages on both TLS 1.2 and TLS 1.3 stream transports.
