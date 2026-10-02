# OpenSSL offers and negotiates obsolete TLS 1.2 signature schemes

## Summary

- Verdict: `confirmed_issue`
- Confidence: `high`
- Root Cause Key: `legacy-sigalgs-compatibility`
- Scope: OpenSSL builds that support TLS 1.3 while retaining TLS 1.2 compatibility

This report contains three issues confirmed by the standard, source code, and runtime behavior:

| Confirmed issue | Severity | Applicable configuration |
| --- | --- | --- |
| A TLS 1.2 ClientHello offers DSA SignatureScheme values, and OpenSSL can negotiate one | `MUST NOT` violation | Default protocol range; completing a handshake requires an available DSA certificate and a security configuration that permits it |
| A TLS 1.2 ClientHello offers SHA-224 SignatureScheme values, and OpenSSL can negotiate one | `MUST NOT` violation | Default protocol range; completing a handshake requires a security configuration that permits SHA-224 |
| Enabled legacy SHA-1 SignatureScheme values are not listed after every other algorithm | `MUST` ordering violation | Configuration-dependent; reproduced with `SECLEVEL=0` |

Keeping historical code points in source is not itself a violation. The problem is that OpenSSL sends or negotiates obsolete/reserved DSA and SHA-224 values. RSA/ECDSA SHA-1 belongs to a different legacy category: it may be offered solely for TLS 1.2 backward compatibility, but it must have the lowest priority.

## Standard Requirement

- Current standard: [RFC 9846, Section 4.3.3, "Signature Algorithms"](https://www.rfc-editor.org/rfc/rfc9846.html#section-4.3.3)
- Original audit standard: [RFC 8446, Section 4.2.3, "Signature Algorithms"](https://www.rfc-editor.org/rfc/rfc8446.html#section-4.2.3)

RFC 9846 retains RFC 8446's rule for obsolete TLS 1.2 hash/signature pairs:

```text
They MUST NOT be offered or negotiated by any implementation.
```

The same section identifies DSA and SHA-224 as prohibited. Consequently, the historical `dsa_sha1_RESERVED`, `dsa_sha256_RESERVED`, `dsa_sha384_RESERVED`, `dsa_sha512_RESERVED`, and SHA-224 combinations must not appear in ClientHello, be selected by a server, or be used for a TLS 1.2 handshake signature.

The rule for RSA/ECDSA SHA-1 is narrower:

```text
Endpoints SHOULD NOT negotiate these algorithms but are permitted to do
so solely for backward compatibility.  Clients offering these values
MUST list them as the lowest priority (listed after all other algorithms
in SignatureSchemeList).
```

The SHA-1 defect is therefore not the existence of the code points or their use for permitted TLS 1.2 compatibility. The defect is their position before other offered SignatureScheme values.

## Relevant Source Code

The built-in TLS 1.2 list at `ssl/t1_lib.c:2051-2086` contains allowed algorithms, legacy SHA-1 algorithms, and obsolete SHA-224/DSA values. It places some SHA-1 entries before later algorithms:

```c
static const uint16_t tls12_sigalgs[] = {
    ...
    TLSEXT_SIGALG_rsa_pkcs1_sha256,
    TLSEXT_SIGALG_rsa_pkcs1_sha384,
    TLSEXT_SIGALG_rsa_pkcs1_sha512,

    TLSEXT_SIGALG_ecdsa_sha224,
    TLSEXT_SIGALG_ecdsa_sha1,

    TLSEXT_SIGALG_rsa_pkcs1_sha224,
    TLSEXT_SIGALG_rsa_pkcs1_sha1,

    TLSEXT_SIGALG_dsa_sha224,
    TLSEXT_SIGALG_dsa_sha1,
    TLSEXT_SIGALG_dsa_sha256,
    TLSEXT_SIGALG_dsa_sha384,
    TLSEXT_SIGALG_dsa_sha512,
    ...
};
```

`ssl/t1_lib.c:2130-2243` supplies TLS 1.2-compatible `SIGALG_LOOKUP` entries for these values, allowing them to enter negotiation and signing paths.

`ssl/t1_lib.c:3443-3464` filters DSA, SHA-1, and SHA-224 together only when the client is restricted to TLS 1.3:

```c
if (!s->server
    && s->s3.tmp.min_ver >= TLS1_3_VERSION
    && lu->sig == EVP_PKEY_DSA)
    continue;
```

A client that supports both TLS 1.2 and TLS 1.3 does not satisfy `min_ver >= TLS1_3_VERSION`, so these values can remain in its offered list.

`ssl/t1_lib.c:3552-3569` serializes compatible SignatureScheme values in list order and does not move SHA-1 behind every other algorithm. The TLS 1.2 selection path at `ssl/t1_lib.c:4779-4844` can then select entries from the shared list for a handshake signature.

## Implementation Behavior

Capturing and decoding ClientHello messages from OpenSSL `4.1.0-dev` produced the following observations:

- With the default protocol range and security level, the client did not offer SHA-1, but it did offer `ecdsa_sha224_RESERVED`, `rsa_pkcs1_sha224_RESERVED`, `dsa_sha224_RESERVED`, `dsa_sha256_RESERVED`, `dsa_sha384_RESERVED`, and `dsa_sha512_RESERVED`.
- Restricting the client to TLS 1.2 while retaining the default security level did not remove those SHA-224 and DSA values.
- With the default protocol range and `SECLEVEL=0`, `ecdsa_sha1` appeared at index 21 and `rsa_pkcs1_sha1` at index 23. DSA values and multiple SLH-DSA SignatureScheme values followed them, so SHA-1 was not at the lowest priority.

Focused TLS 1.2 handshakes further showed that the obsolete values were not merely present in source code or ClientHello:

- OpenSSL completed a handshake using `dsa_sha256`.
- OpenSSL completed a handshake using `ecdsa_sha224`.

## Inconsistency Reason

The standard treats these values differently, but OpenSSL places them in the same TLS 1.2 compatibility list and selection path:

1. DSA and SHA-224 historical values are obsolete/reserved. They must not be offered, negotiated, or used, but OpenSSL sends them from the default multi-version client configuration and can use them in TLS 1.2 handshakes.
2. RSA/ECDSA SHA-1 can be enabled solely for TLS 1.2 backward compatibility, but every SHA-1 value must follow every non-SHA-1 value. Restoring SHA-1 through `SECLEVEL=0` preserves the built-in list order and violates that requirement.

## Runtime Evidence

The ClientHello test ran OpenSSL `4.1.0-dev` under three configurations, captured the emitted handshake bytes, decoded the `signature_algorithms` vector, and recorded each scheme's position:

| Configuration | Observed SHA-224/DSA values | Observed SHA-1 values | Result |
| --- | --- | --- | --- |
| Default protocol range and default security level | Present at indexes 20-25 | Not offered | Confirms prohibited DSA/SHA-224 offering |
| TLS 1.2 only and default security level | Present at indexes 14-19 | Not offered | Confirms prohibited DSA/SHA-224 offering |
| Default protocol range and `SECLEVEL=0` | Present at indexes 20, 22, and 24-28 | Present at indexes 21 and 23 | Other algorithms follow SHA-1, so the lowest-priority rule is not met |

Two focused server/client tests then supplied certificates and security settings that made the prohibited choices usable:

- The DSA case negotiated TLS 1.2 with cipher `DHE-DSS-AES256-GCM-SHA384`. The client reported `Peer signing digest: SHA256`, `Peer signature type: dsa_sha256`, and `Verify return code: 0 (ok)`.
- The SHA-224 case negotiated TLS 1.2. The client reported `Peer signing digest: SHA224`, `Peer signature type: ecdsa_sha224`, and `Verify return code: 0 (ok)`.

These observations cover both the actual offer order and actual negotiated use; the conclusion does not rely only on constants or enumeration entries.

## Impact

- A strict RFC 8446 or RFC 9846 peer may ignore the reserved values or abort after OpenSSL selects DSA or SHA-224, causing interoperability failures.
- Deployments that permit weak algorithms can complete TLS 1.2 handshake signatures that the standard explicitly prohibits.
- Under `SECLEVEL=0`, SHA-1 may be selected before other available schemes because it is not placed at the required lowest priority.

## Fix Direction

1. Remove SHA-224 and DSA SignatureScheme values from ClientHello construction and TLS 1.2 signature selection whenever the implementation supports TLS 1.3 and TLS 1.2; filtering only TLS 1.3-only clients is insufficient.
2. If RSA/ECDSA SHA-1 remains available for TLS 1.2 compatibility, move every SHA-1 value to the end of the final list after built-in and provider schemes have been merged.
3. Add regression tests asserting that ClientHello contains no SHA-224 or DSA values, every enabled SHA-1 value follows every non-SHA-1 value, and the server cannot select or generate DSA/SHA-224 TLS 1.2 handshake signatures.
