# OpenSSL can send 0-RTT with a non-first PSK identity

## Summary

OpenSSL has a reachable client-side TLS 1.3 path where `early_data` is sent while the first `OfferedPsks.identities` entry is a resumption ticket that is not used for 0-RTT. The actual 0-RTT keys are derived from a later external PSK identity.

This is a confirmed RFC 8446 mismatch for ClientHello construction. In the runtime check, OpenSSL's server selected the second PSK identity and rejected early data, so the confirmed issue is the client's emitted 0-RTT identity ordering, not server acceptance of early data for identity 1.


## Standard Requirement

Official standard: [RFC 8446, Sections 4.2.10 and 4.2.11](https://www.rfc-editor.org/rfc/rfc8446.html#section-4.2.10).

RFC 8446, Section 4.2.11, "Pre-Shared Key Extension", defines the client-side `identities` list:

```text
identities:  A list of the identities that the client is willing to
   negotiate with the server.  If sent alongside the "early_data"
   extension (see Section 4.2.10), the first identity is the one used
   for 0-RTT data.
```

RFC 8446, Section 4.2.10, "Early Data Indication", also constrains acceptance:

```text
In order to accept early data, the server MUST have accepted a PSK
cipher suite and selected the first key offered in the client's
"pre_shared_key" extension.
```

RFC 8446, Section 4.2.11, separately requires the client to reject a server that accepts early data with a nonzero `selected_identity`:

```text
If the server supplies an "early_data" extension, the client MUST
verify that the server's selected_identity is 0.
```

Interpretation: when `early_data` and `pre_shared_key` appear together in ClientHello, identity index 0 is the 0-RTT PSK. A client must not send early data under keys derived from a later identity while leaving another usable identity first.

## Relevant Source Code

### `ssl/statem/extensions_clnt.c:1150`

```c
if (s->early_data_state != SSL_EARLY_DATA_CONNECTING
    || (s->session->ext.max_early_data == 0
        && (psksess == NULL || psksess->ext.max_early_data == 0))) {
    s->max_early_data = 0;
    return EXT_RETURN_NOT_SENT;
}
edsess = s->session->ext.max_early_data != 0 ? s->session : psksess;
s->max_early_data = edsess->ext.max_early_data;
```

When the stored resumption session has `max_early_data == 0` but an external PSK session has `max_early_data > 0`, the client chooses the external PSK session as the early-data session.

### `ssl/statem/extensions_clnt.c:1200`

```c
if (!WPACKET_put_bytes_u16(pkt, TLSEXT_TYPE_early_data)
    || !WPACKET_start_sub_packet_u16(pkt)
    || !WPACKET_close(pkt)) {
    SSLfatal(s, SSL_AD_INTERNAL_ERROR, ERR_R_INTERNAL_ERROR);
    return EXT_RETURN_FAIL;
}
```

After selecting the external PSK session for early data, the client emits the `early_data` extension.

### `ssl/statem/extensions_clnt.c:1544`

```c
if (dores) {
    if (!WPACKET_sub_memcpy_u16(pkt, s->session->ext.tick,
            s->session->ext.ticklen)
        || !WPACKET_put_bytes_u32(pkt, agems)) {
        SSLfatal(s, SSL_AD_INTERNAL_ERROR, ERR_R_INTERNAL_ERROR);
        return EXT_RETURN_FAIL;
    }
}

if (s->psksession != NULL) {
    if (!WPACKET_sub_memcpy_u16(pkt, s->psksession_id,
            s->psksession_id_len)
        || !WPACKET_put_bytes_u32(pkt, 0)) {
        SSLfatal(s, SSL_AD_INTERNAL_ERROR, ERR_R_INTERNAL_ERROR);
        return EXT_RETURN_FAIL;
    }
    s->ext.tick_identity++;
}
```

The `pre_shared_key` extension serializes the resumption ticket first when `dores` is set, then serializes the external PSK identity.

### `ssl/statem/extensions.c:1886`

```c
if (external
    && s->early_data_state == SSL_EARLY_DATA_CONNECTING
    && s->session->ext.max_early_data == 0
    && sess->ext.max_early_data > 0)
    usepskfored = 1;
```

This records that the external PSK is being used for early data when the regular session is not early-data-capable.

### `ssl/statem/extensions.c:1908`

```c
if (s->server || !external || usepskfored)
    early_secret = (unsigned char *)s->early_secret;
else
    early_secret = (unsigned char *)sess->early_secret;
```

For the external-PSK early-data case, the early secret used by the connection is derived from the external PSK.

### `ssl/tls13_enc.c:553`

```c
if (s->early_data_state == SSL_EARLY_DATA_CONNECTING
    && s->max_early_data > 0
    && s->session->ext.max_early_data == 0) {
    if (!ossl_assert(s->psksession != NULL
            && s->max_early_data == s->psksession->ext.max_early_data)) {
        SSLfatal(s, SSL_AD_INTERNAL_ERROR, ERR_R_INTERNAL_ERROR);
        goto err;
    }
    sslcipher = SSL_SESSION_get0_cipher(s->psksession);
}
```

The TLS 1.3 early traffic key path also confirms that, in this condition, the client uses the external PSK session for early-data encryption.

## Implementation Behavior

The implementation can enter this state:

1. The client has a usable TLS 1.3 resumption ticket with `max_early_data == 0`.
2. The client also has an external PSK session with `max_early_data > 0`.
3. `tls_construct_ctos_early_data()` selects the external PSK session for 0-RTT and emits `early_data`.
4. `tls_construct_ctos_psk()` still serializes the resumption ticket as identity 0 and the external PSK as identity 1.
5. The early traffic keys are derived from the external PSK, so the 0-RTT data corresponds to identity 1, not identity 0.

This conflicts with the RFC requirement that, when `early_data` is sent alongside `pre_shared_key`, the first identity is the one used for 0-RTT data.

## Runtime Evidence

A focused runtime check was run against the OpenSSL build from the repository root using the existing `apps/openssl` binary and test certificates. The check created:

- a resumption session whose `Max Early Data` was `0`
- an external PSK session whose `Max Early Data` was `256`
- a target client connection using both `-sess_in` and `-psk_session`, plus `-early_data`

The ClientHello trace was parsed from the runtime output. Key result:

```text
session preconditions:
  resumption: Max Early Data: 0
  external:   Max Early Data: 256

clienthello extensions:
  early_data extension: yes
  psk extension length 330: yes
  parsed psk payload bytes: 330
  identities vector length: 228
  identity count: 2
  identity[0]: len=208 value=<opaque ticket> obfuscated_ticket_age=2132505699
  identity[1]: len=8 value=EXTIDENT obfuscated_ticket_age=0
  binders vector length: 98
  sent early ApplicationData record: yes

server response:
  selected_identity=1 in ServerHello: yes
  server early data status: rejected
```

The runtime result proves that OpenSSL emitted a ClientHello containing both `early_data` and `pre_shared_key`, with the resumption ticket at identity 0 and the external PSK `EXTIDENT` at identity 1. The client also sent an early `ApplicationData` record. Therefore the early data could only correspond to the external PSK at identity 1, while RFC 8446 assigns 0-RTT data to identity 0.

The server-side result is useful context: OpenSSL's server selected identity 1 for PSK use and rejected early data. That behavior avoids accepting 0-RTT under the second identity, but it does not repair the client's non-compliant ClientHello construction.

## Inconsistency Reason

RFC 8446 gives `OfferedPsks.identities[0]` a special meaning whenever the same ClientHello includes `early_data`: it is the 0-RTT PSK. OpenSSL can instead construct a ClientHello where:

- identity 0 is the resumption ticket
- identity 1 is the external PSK
- `early_data` is present
- early traffic keys are derived from the external PSK
- an early `ApplicationData` record is sent

That makes a later identity, not the first identity, the effective 0-RTT PSK.

## Impact

The primary impact is interoperability and standards compliance. A strict TLS 1.3 peer is entitled to treat early data as tied to identity 0. In this mixed resumption-ticket plus external-PSK configuration, the wire message advertises identity 0 as the 0-RTT identity while the client's early-data keys are derived from identity 1.

OpenSSL's own server rejected early data in the tested run after selecting identity 1, so this evidence does not show server-side acceptance of invalid 0-RTT. The confirmed issue is the client emitting an inconsistent 0-RTT ClientHello and early-data record.

## Fix Direction

When `early_data` will be sent using an external PSK, the external PSK identity should be identity 0 in `OfferedPsks`, or the client should suppress `early_data` when another identity must remain first. The PSK binder order and `selected_identity` tracking should be adjusted consistently with any identity reordering.
