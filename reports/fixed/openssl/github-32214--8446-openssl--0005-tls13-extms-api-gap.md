# OpenSSL issue: TLS 1.3 EXTMS API reporting gap

## Summary

OpenSSL successfully negotiates TLS 1.3, but `SSL_get_extms_support()` reports
`0` after the TLS 1.3 handshake. This is a RFC 8446 Appendix D `SHOULD`-level
API reporting gap, not a TLS 1.3 wire-protocol or key-schedule failure.

## Standard Requirement

Standard reference: [RFC 8446, Section 1.1](https://www.rfc-editor.org/rfc/rfc8446.html#section-1.1) and [Appendix D, "Backward Compatibility"](https://www.rfc-editor.org/rfc/rfc8446.html#appendix-D).

RFC 8446 Section 1.1 makes uppercase `SHOULD` normative through BCP 14. The
relevant Appendix D paragraph says:

```text
TLS 1.2 and prior supported an "Extended Master Secret" [RFC7627]
extension which digested large parts of the handshake transcript into
the master secret.  Because TLS 1.3 always hashes in the transcript
up to the server Finished, implementations which support both TLS 1.3
and earlier versions SHOULD indicate the use of the Extended Master
Secret extension in their APIs whenever TLS 1.3 is used.
```

The requirement is about API reporting. It does not require TLS 1.3 to negotiate
the legacy EMS extension on the wire. Instead, because TLS 1.3 already includes
the transcript up to the server Finished in its key schedule, an implementation
that also supports earlier TLS versions should indicate EMS use through APIs
whenever a TLS 1.3 connection is used.

## Relevant Source Code

`SSL_CTRL_GET_EXTMS_SUPPORT` reports only the legacy session flag and has no
TLS 1.3 special case.

### `ssl/ssl_lib.c:3217-3223`

```c
    case SSL_CTRL_GET_EXTMS_SUPPORT:
        if (!sc->session || SSL_in_init(s) || ossl_statem_get_in_handshake(sc))
            return -1;
        if (sc->session->flags & SSL_SESS_FLAG_EXTMS)
            return 1;
        else
            return 0;
```

The session flag is populated only from `TLS1_FLAGS_RECEIVED_EXTMS`.

### `ssl/ssl_sess.c:484-493`

```c
    memcpy(ss->sid_ctx, s->sid_ctx, s->sid_ctx_length);
    ss->sid_ctx_length = s->sid_ctx_length;
    s->session = ss;
    ss->ssl_version = s->version;
    ss->verify_result = X509_V_OK;

    /* If client supports extended master secret set it in session */
    if (s->s3.flags & TLS1_FLAGS_RECEIVED_EXTMS)
        ss->flags |= SSL_SESS_FLAG_EXTMS;
```

The EMS extension itself is registered as TLS 1.2-and-below only.

### `ssl/statem/extensions.c:333-338`

```c
    { TLSEXT_TYPE_extended_master_secret,
        SSL_EXT_CLIENT_HELLO | SSL_EXT_TLS1_2_SERVER_HELLO
            | SSL_EXT_TLS1_2_AND_BELOW_ONLY,
        OSSL_ECH_HANDLING_COMPRESS,
        init_ems, tls_parse_ctos_ems, tls_parse_stoc_ems,
        tls_construct_stoc_ems, tls_construct_ctos_ems, final_ems },
```

The public API documentation defines the function as reporting whether the
current session used Extended Master Secret.

### `doc/man3/SSL_get_extms_support.pod:15-24`

```text
SSL_get_extms_support() indicates whether the current session used extended
master secret.

SSL_get_extms_support() returns 1 if the current session used extended
master secret, 0 if it did not and -1 if a handshake is currently in
progress i.e. it is not possible to determine if extended master secret
was used.
```

## Implementation Behavior

OpenSSL's TLS 1.3 handshake behavior is compatible with the standard's wire
format expectations: the legacy EMS extension is not negotiated for TLS 1.3.
However, the API path still reports EMS use only by checking
`SSL_SESS_FLAG_EXTMS`. Since TLS 1.3 does not negotiate the legacy extension,
the session flag is not set, and `SSL_get_extms_support()` returns `0`.

This means the implementation supports both TLS 1.2 and TLS 1.3, but the public
API does not indicate EMS use when TLS 1.3 is used.

## Inconsistency Reason

RFC 8446 Appendix D says implementations that support TLS 1.3 and earlier
versions `SHOULD` indicate EMS use through their APIs whenever TLS 1.3 is used.
OpenSSL's API instead reports only the legacy EMS session flag. Because that
flag is tied to the TLS 1.2-and-below EMS extension, TLS 1.3 handshakes are
reported as not using EMS even though RFC 8446 says TLS 1.3 should be indicated
as EMS-like at the API layer.

The mismatch is therefore limited to API reporting. The runtime evidence below
does not show a TLS 1.3 negotiation failure; it shows a completed TLS 1.3
handshake followed by an API return value of `0`.

## Runtime Evidence

The test linked an in-memory client/server probe against OpenSSL `4.1.0-dev` on `linux-x86_64`. For each case it restricted both peers to one protocol version, completed the handshake, and queried `SSL_get_extms_support()` on both endpoints. TLS 1.2 served as the positive control; TLS 1.3 was the target case. The process exited with code 0 and produced no diagnostic output.

Observed output:

```text
tls12 negotiated_client=TLSv1.2 negotiated_server=TLSv1.2 client_extms=1 server_extms=1
tls13 negotiated_client=TLSv1.3 negotiated_server=TLSv1.3 client_extms=0 server_extms=0
```

The TLS 1.2 positive control confirms that the API returns `1` when the legacy
EMS flag is set. The TLS 1.3 run confirms the issue: both peers complete a
TLS 1.3 handshake, but `SSL_get_extms_support()` returns `0`.

## Impact

Applications that rely on `SSL_get_extms_support()` to determine whether a
connection has EMS-style transcript binding will receive a negative result for
TLS 1.3 connections. This may cause misleading diagnostics, incorrect policy
decisions, or unnecessary compatibility workarounds in applications that expect
the RFC 8446 Appendix D API indication.

## Fix Direction

Adjust the EXTMS reporting API so that a completed TLS 1.3 session reports EMS
use. A narrowly scoped fix would make `SSL_CTRL_GET_EXTMS_SUPPORT` return `1`
when the negotiated protocol version is TLS 1.3, while preserving the existing
legacy `SSL_SESS_FLAG_EXTMS` behavior for TLS 1.2 and below.
