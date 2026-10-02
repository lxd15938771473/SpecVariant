# TLS 1.3 certificate_authorities accepts and emits an empty authorities vector


OpenSSL accepts a TLS 1.3 `CertificateRequest` containing a `certificate_authorities(47)` extension whose inner `CertificateAuthoritiesExtension.authorities` vector has length 0. OpenSSL can also generate the same empty vector when a server has a non-empty CA list but `SSL_OP_DISABLE_TLSEXT_CA_NAMES` is enabled through `s_server -no_ca_names`.

This replaces the previous `suspected_issue` / `unresolved` conclusion. The confirmed non-compliance is the missing lower-bound enforcement for the `authorities<3..2^16-1>` vector. The `oid_filters` portion of the original family remains unconfirmed because this OpenSSL snapshot does not expose a built-in `oid_filters` extension parser or constructor, and absence of that optional built-in path is not enough to prove the same protocol violation.

## Standard Requirement

Official standard: [RFC 8446, Section 4.2.4, "Certificate Authorities"](https://www.rfc-editor.org/rfc/rfc8446.html#section-4.2.4) and [Section 6, "Alert Protocol"](https://www.rfc-editor.org/rfc/rfc8446.html#section-6).

```text
opaque DistinguishedName<1..2^16-1>;

struct {
    DistinguishedName authorities<3..2^16-1>;
} CertificateAuthoritiesExtension;
```

```text
Peers which receive a message which cannot be parsed according to the syntax
(e.g., have a length extending beyond the message boundary or contain an
out-of-range length) MUST terminate the connection with a "decode_error" alert.
```

RFC 8446 Section 3 defines variable-length vector bounds as an inclusive legal length range. Therefore, the encoded `CertificateAuthoritiesExtension.authorities` vector must have an actual length of at least 3 bytes. An inner vector length of 0 is syntactically out of range and should be rejected with `decode_error`.

## Relevant Source Code

### `ssl/statem/extensions.c:438-449`

```c
{
    TLSEXT_TYPE_certificate_authorities,
    SSL_EXT_CLIENT_HELLO | SSL_EXT_TLS1_3_CERTIFICATE_REQUEST
        | SSL_EXT_TLS1_3_ONLY,
    OSSL_ECH_HANDLING_COMPRESS,
    init_certificate_authorities,
    tls_parse_certificate_authorities,
    tls_parse_certificate_authorities,
    tls_construct_certificate_authorities,
    tls_construct_certificate_authorities,
    NULL,
},
```

OpenSSL registers `certificate_authorities` as a built-in TLS 1.3 extension for `ClientHello` and `CertificateRequest`, with both parse and construct handlers.

### `ssl/statem/extensions.c:1638-1672`

```c
const STACK_OF(X509_NAME) *ca_sk = get_ca_names(s);

if (ca_sk == NULL || sk_X509_NAME_num(ca_sk) == 0)
    return EXT_RETURN_NOT_SENT;

if (!construct_ca_names(s, ca_sk, pkt)) {
    return EXT_RETURN_FAIL;
}

if (!parse_ca_names(s, pkt))
    return 0;
if (PACKET_remaining(pkt) != 0) {
    SSLfatal(s, SSL_AD_DECODE_ERROR, SSL_R_BAD_EXTENSION);
    return 0;
}
return 1;
```

The constructor decides whether to send the extension before `construct_ca_names()` applies `SSL_OP_DISABLE_TLSEXT_CA_NAMES`. The parser delegates the whole extension body to `parse_ca_names()` and only checks for trailing bytes afterward.

### `ssl/statem/statem_lib.c:2711-2747`

```c
if (!PACKET_get_length_prefixed_2(pkt, &cadns)) {
    SSLfatal(s, SSL_AD_DECODE_ERROR, SSL_R_LENGTH_MISMATCH);
    goto err;
}

while (PACKET_remaining(&cadns)) {
    const unsigned char *namestart, *namebytes;
    unsigned int name_len;

    if (!PACKET_get_net_2(&cadns, &name_len)
        || !PACKET_get_bytes(&cadns, &namebytes, name_len)) {
        SSLfatal(s, SSL_AD_DECODE_ERROR, SSL_R_LENGTH_MISMATCH);
        goto err;
    }
}

sk_X509_NAME_pop_free(s->s3.tmp.peer_ca_names, X509_NAME_free);
s->s3.tmp.peer_ca_names = ca_sk;

return 1;
```

`parse_ca_names()` accepts the two-byte inner vector length and then loops while bytes remain. If the length is 0, the loop body is skipped and the function succeeds with an empty CA-name stack. No check enforces the RFC lower bound of 3.

### `ssl/statem/statem_lib.c:2775-2805`

```c
if (!WPACKET_start_sub_packet_u16(pkt)) {
    SSLfatal(s, SSL_AD_INTERNAL_ERROR, ERR_R_INTERNAL_ERROR);
    return 0;
}

if ((ca_sk != NULL) && !(s->options & SSL_OP_DISABLE_TLSEXT_CA_NAMES)) {
    int i;

    for (i = 0; i < sk_X509_NAME_num(ca_sk); i++) {
        unsigned char *namebytes;
        X509_NAME *name = sk_X509_NAME_value(ca_sk, i);
        int namelen;
        /* DistinguishedName entries are encoded here. */
    }
}

if (!WPACKET_close(pkt)) {
    SSLfatal(s, SSL_AD_INTERNAL_ERROR, ERR_R_INTERNAL_ERROR);
    return 0;
}
```

`construct_ca_names()` always opens the inner `uint16` vector. When `SSL_OP_DISABLE_TLSEXT_CA_NAMES` is set, it skips every CA name and closes an empty vector.

### `apps/s_server.c:2679-2680` and `apps/s_server.c:3069-3073`

```c
if (no_ca_names) {
    SSL_CTX_set_options(ctx, SSL_OP_DISABLE_TLSEXT_CA_NAMES);
}
```

```c
if (CAfile != NULL) {
    SSL_CTX_set_client_CA_list(ctx, SSL_load_client_CA_file(CAfile));
}
```

`s_server -CAfile ... -no_ca_names` can therefore create the relevant state: a non-empty client CA list causes `tls_construct_certificate_authorities()` to send the extension, while `-no_ca_names` causes `construct_ca_names()` to emit an empty inner vector.

## Implementation Behavior

Parsing behavior:

- A malformed TLS 1.3 `CertificateRequest` with `certificate_authorities(47)` and inner `authorities` length 0 is accepted.
- The client continues the handshake instead of aborting with `decode_error`.
- The empty CA list is stored as `s->s3.tmp.peer_ca_names`.

Generation behavior:

- With a configured CA file, `get_ca_names()` returns a non-empty CA stack, so the extension is selected for transmission.
- With `SSL_OP_DISABLE_TLSEXT_CA_NAMES`, the constructor suppresses every DistinguishedName entry after the extension has already been selected.
- The resulting extension body contains only the two-byte inner vector length field set to 0.

`oid_filters` behavior:

- Source search found `trusted_ca_keys` and `certificate_authorities` constants, but no built-in `oid_filters` extension definition, parser, or constructor in the inspected SSL extension registry.
- This means the previous broad `oid_filters` family finding should not be treated as confirmed by this evidence. It is separate from the confirmed `certificate_authorities` vector-bound issue.

## Inconsistency Reason

RFC 8446 requires `CertificateAuthoritiesExtension.authorities` to have an encoded vector length in the inclusive range `3..2^16-1`. A zero-length vector is out of range and should trigger `decode_error` when received.

OpenSSL does not enforce that lower bound on receive: `parse_ca_names()` accepts the length-prefixed vector and succeeds when it is empty. OpenSSL also violates the construction side in a reachable configuration: `tls_construct_certificate_authorities()` decides to send the extension based on a non-empty CA stack, but `construct_ca_names()` can still serialize an empty inner vector when CA-name output is disabled.

The implementation is therefore inconsistent with the TLS 1.3 vector syntax and the specific `certificate_authorities` grammar.

## Runtime Evidence

The focused TLS 1.3 test exercised both the receive and transmit paths against OpenSSL `4.1.0-dev`.

For the receive-side case, the test intercepted a server `CertificateRequest` and replaced its `certificate_authorities(47)` body with a syntactically complete extension whose inner `authorities` vector length was zero. The client accepted the message, completed the handshake, and sent no fatal alert. The observed result was:

```text
Injected certificate_authorities(47) with inner authorities length 0; originally_present=1
ok 4 - handshake succeeded after empty certificate_authorities vector
```

For the transmit-side case, the test configured `s_server` with a non-empty CA file and `-no_ca_names`, captured the generated `CertificateRequest`, and decoded extension 47. OpenSSL emitted an extension body of two bytes with `inner_len=0`; the peer accepted it and the handshake completed. The observed result was:

```text
Observed certificate_authorities(47) from -no_ca_names with ext_data_len=2 inner_len=0
ok 8 - -no_ca_names emitted an empty authorities vector
ok 9 - handshake succeeded with OpenSSL-generated empty certificate_authorities vector
ok 10 - client did not send fatal alert for OpenSSL-generated empty vector
All tests successful.
Result: PASS
```

## Impact

A strict TLS 1.3 peer may reject OpenSSL-generated `CertificateRequest` messages that contain an empty `certificate_authorities` vector. Conversely, OpenSSL accepts a syntactically invalid `CertificateRequest` that should be rejected with `decode_error`. The most likely practical impact is interoperability failure with strict implementations and reduced conformance to RFC 8446 parsing requirements.

## Fix Direction

- Enforce the `authorities<3..2^16-1>` lower bound on the TLS 1.3 `certificate_authorities` parse path before accepting the vector.
- Avoid constructing `certificate_authorities` when `SSL_OP_DISABLE_TLSEXT_CA_NAMES` would leave the inner vector empty.
- Add focused tests for both receive-side rejection of an empty `authorities` vector and send-side behavior with a configured CA file plus disabled CA-name emission.
