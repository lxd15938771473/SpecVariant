# OpenSSL retains consumed TLS 1.3 stage secrets

## Summary

- Verdict: `issue_found` as an RFC `SHOULD`-level hardening gap
- Confidence: high for the observed retention behavior; medium for severity because the standard uses `SHOULD`, not `MUST`
- Covered Record IDs: `cand-4450aafb8148-baseline`, `cand-4a3bc313e7e5-duplicate`, `cand-62a8ea3b1a0b-missing`
- Root Cause Key: `tls13-secret-retention`

OpenSSL correctly derives the TLS 1.3 key schedule, but consumed stage secrets such as `early_secret`, `handshake_secret`, and `master_secret` remain in `SSL_CONNECTION` storage after their dependent TLS 1.3 values have been derived. A focused TLS 1.3 memory-BIO handshake probe confirms that these fields are still nonzero after handshake completion and after a successful `SSL_clear()`.

The original candidate wording should not say that RFC 8446 makes this a hard `MUST` requirement. RFC 8446 says the secret `SHOULD be erased`, so this finding is a real recommendation-level security issue unless OpenSSL has a documented reason to retain the consumed secrets.

## Standard Requirement

Official references:

- [RFC 8446, Section 7.1, "Key Schedule"](https://www.rfc-editor.org/rfc/rfc8446.html#section-7.1)
- [RFC 2119, Section 3, "`SHOULD`"](https://www.rfc-editor.org/rfc/rfc2119.html#section-3)

RFC 8446 states that uppercase requirement keywords are interpreted under BCP 14 / RFC 2119 / RFC 8174. In Section 7.1, immediately after the TLS 1.3 key schedule diagram, it says:

```text
Once all the values which are to be derived from a given secret have
been computed, that secret SHOULD be erased.
```

Interpretation: this is not a `MUST`-level interoperability requirement. It is a `SHOULD` / recommended security requirement: an implementation may deviate only with a valid reason and after weighing the consequences.

## Relevant Source Code

`ssl/ssl_local.h:1529-1540` stores TLS 1.3 secrets directly in `SSL_CONNECTION`, outside the `s3` sub-structure:

```c
unsigned char early_secret[EVP_MAX_MD_SIZE];
unsigned char handshake_secret[EVP_MAX_MD_SIZE];
unsigned char master_secret[EVP_MAX_MD_SIZE];
unsigned char resumption_master_secret[EVP_MAX_MD_SIZE];
unsigned char client_app_traffic_secret[EVP_MAX_MD_SIZE];
unsigned char server_app_traffic_secret[EVP_MAX_MD_SIZE];
unsigned char exporter_master_secret[EVP_MAX_MD_SIZE];
```

`ssl/tls13_enc.c:235-264` derives `handshake_secret` from `early_secret`, then derives `master_secret` from `handshake_secret`:

```c
return tls13_generate_secret(s, ssl_handshake_md(s), s->early_secret,
    insecret, insecretlen, (unsigned char *)&s->handshake_secret);

return tls13_generate_secret(s, md, prev, NULL, 0, out);
```

`ssl/tls13_enc.c:628-675` later reads the stored `handshake_secret` and `master_secret` to derive traffic secrets:

```c
insecret = s->handshake_secret;
...
insecret = s->master_secret;
```

`ssl/tls13_enc.c:783-784` and `ssl/tls13_enc.c:849-850` cleanse temporary working buffers only:

```c
OPENSSL_cleanse(key, sizeof(key));
OPENSSL_cleanse(secret, sizeof(secret));
```

`ssl/s3_lib.c:3921-3923` clears `sc->s3`, but the TLS 1.3 secret arrays are not inside `sc->s3`:

```c
flags = sc->s3.flags & (TLS1_FLAGS_QUIC | TLS1_FLAGS_QUIC_INTERNAL);
memset(&sc->s3, 0, sizeof(sc->s3));
sc->s3.flags |= flags;
```

`ssl/ssl_lib.c:1469-1477` ultimately frees the `SSL` allocation with `OPENSSL_free(s)`, not `OPENSSL_clear_free(...)`:

```c
if (s->method != NULL)
    s->method->ssl_free(s);
...
OPENSSL_free(s);
```

A source search found no `OPENSSL_cleanse(...)` or `OPENSSL_clear_free(...)` call that directly targets `early_secret`, `handshake_secret`, `master_secret`, `resumption_master_secret`, `exporter_master_secret`, or traffic-secret fields in the TLS implementation.

## Implementation Behavior

The implementation keeps TLS 1.3 stage secrets in persistent connection fields. It cleanses temporary `key` and `secret` buffers used during derivation, but it does not eagerly erase the consumed stage secrets from `SSL_CONNECTION` after the dependent values have been computed.

The strongest evidence applies to `early_secret`, `handshake_secret`, and `master_secret`. Current application traffic secrets, exporter secrets, and resumption secrets may remain legitimately live for KeyUpdate, exporter APIs, or NewSessionTicket processing, so they should be treated separately when designing a fix.

## Inconsistency Reason

RFC 8446 recommends erasing a secret after all values derived from that secret have been computed. OpenSSL retains consumed stage secrets in connection state after the handshake, and `SSL_clear()` does not clear those fields. That behavior is inconsistent with the RFC `SHOULD` recommendation unless OpenSSL intentionally accepts and documents the retention tradeoff.

This is not a `MUST`-level protocol failure. It is a concrete `SHOULD`-level security hardening gap with runtime evidence.

## Runtime Evidence

The focused runtime probe creates a TLS 1.3 client and server using memory BIOs, completes the handshake, unwraps the internal `SSL_CONNECTION` objects through OpenSSL internal headers, and checks whether selected TLS 1.3 secret arrays contain any nonzero byte. It then calls `SSL_clear()` on both peers and checks the same fields again.

The handshake completed successfully on both peers. Before and after successful `SSL_clear()` calls, the probe observed nonzero bytes in `early_secret`, `handshake_secret`, and `master_secret` on both the client and server:

```text
handshake_complete=1
client_version=0x0304
server_version=0x0304
client_after_handshake_early_secret_nonzero=1
client_after_handshake_handshake_secret_nonzero=1
client_after_handshake_master_secret_nonzero=1
server_after_handshake_early_secret_nonzero=1
server_after_handshake_handshake_secret_nonzero=1
server_after_handshake_master_secret_nonzero=1
client_SSL_clear_return=1
server_SSL_clear_return=1
client_after_SSL_clear_early_secret_nonzero=1
client_after_SSL_clear_handshake_secret_nonzero=1
client_after_SSL_clear_master_secret_nonzero=1
server_after_SSL_clear_early_secret_nonzero=1
server_after_SSL_clear_handshake_secret_nonzero=1
server_after_SSL_clear_master_secret_nonzero=1
```

The run confirms a successful TLS 1.3 negotiation (`0x0304`) and shows that consumed stage secrets remain nonzero after the handshake and after successful `SSL_clear()` on both client and server.

## Impact

The issue does not change wire behavior and should not cause TLS interoperability failures. The risk is memory-exposure hardening: if process memory is later disclosed, secrets that the RFC recommends erasing may remain available longer than necessary.

## Fix Direction

Add explicit cleansing for consumed TLS 1.3 stage-secret fields at points where all downstream values have been derived. The fix should distinguish stage secrets from values that remain live for valid APIs or protocol mechanisms:

- cleanse `early_secret` only after all binder, early traffic, early exporter, derived, and handshake-secret uses are complete;
- cleanse `handshake_secret` only after the master secret and handshake traffic / Finished-dependent values no longer need it;
- cleanse `master_secret` only after application, exporter, and resumption master values have been derived;
- keep application traffic, exporter, and resumption secrets until their legitimate KeyUpdate, exporter API, or ticket-generation uses end.

Add a regression test similar to the runtime probe that completes a TLS 1.3 handshake and verifies consumed stage-secret fields are cleared at the expected lifecycle boundary.
