# X.509 Certificate-Signature OID And RSA-PSS Parameter Checks

## Summary

OpenSSL correctly rejects a TLS `CertificateVerify` SignatureScheme whose RSAE/PSS family conflicts with the end-entity certificate public-key OID. The issue is narrower: for signatures that appear inside the X.509 certificate chain, OpenSSL accepts certificate signatures that do not satisfy the stricter TLS 1.3 `SignatureScheme` rules from RFC 8446.

Two non-compliant chains were accepted in fresh runtime tests:

- A CA certificate has an `rsassaPss` public key with SHA256/MGF1-SHA256 and minimum salt length 32, but the EE certificate is signed with RSA-PSS salt length 64.
- A TLS client advertises only `rsa_pss_pss_sha256`, but accepts an EE certificate whose certificate signature was produced by a CA key using the `rsaEncryption` public-key OID.

This is a TLS policy enforcement gap, not a claim that generic X.509 verification must reject every RFC 4055-compatible RSA-PSS signature outside TLS.

## Standard Basis

Official standard: [RFC 8446, Section 4.2.3, "Signature Algorithms"](https://www.rfc-editor.org/rfc/rfc8446.html#section-4.2.3).

RFC 8446 Section 4.2.3 says `signature_algorithms_cert` applies to signatures in certificates. If that extension is absent, `signature_algorithms` also applies to certificate signatures. It also states that keys found in certificates must be of an appropriate type for the signature algorithms used with them, calling out RSA/PSS specifically.

Relevant requirements:

- `rsa_pss_rsae_*` uses RSASSA-PSS with MGF1. The signed digest and MGF1 digest must both be the corresponding hash, salt length must equal the hash output length, and an X.509 public key must use the `rsaEncryption` OID.
- `rsa_pss_pss_*` uses RSASSA-PSS with MGF1. The signed digest and MGF1 digest must both be the corresponding hash, salt length must equal the hash output length, and an X.509 public key must use the `RSASSA-PSS` OID.
- For `rsa_pss_pss_*` used in certificate signatures, AlgorithmIdentifier parameters must be DER encoded. If the corresponding public key's parameters are present, the certificate signature parameters must be identical to them.

Important distinction:

- RFC 4055's generic RSA-PSS X.509 rule is looser: all public-key and signature parameters must match except `saltLength`, and signature `saltLength` may be greater than or equal to the public-key value.
- RFC 5756 clarifies placement of RSA-PSS parameters in certificate fields.
- RFC 8446 imposes stricter TLS `SignatureScheme` semantics for certificate signatures advertised through `signature_algorithms_cert` or, when that extension is absent, `signature_algorithms`.

Therefore, accepting `saltLength=64` for a TLS `rsa_pss_*_sha256` certificate signature is non-compliant with RFC 8446, even though it can still be compatible with the looser generic RFC 4055 baseline.

## Code Evidence

CertificateVerify OID-family checks are present:

- `ssl/t1_lib.c:2160-2188` maps `rsa_pss_rsae_*` to the RSA certificate key slot and `rsa_pss_pss_*` to the RSA-PSS certificate key slot.
- `ssl/t1_lib.c:2880-2884` checks that the peer CertificateVerify SignatureScheme is consistent with the decoded public-key OID family.
- Existing `test/recipes/70-test_sslsigalgs.t` covers the adjacent CertificateVerify mismatch case.

Certificate-signature OID-family checks are incomplete:

- `ssl/t1_lib.c:4623-4664` checks a certificate's signature using `X509_get_signature_info()`.
- The code comment in that block explicitly says it does not differentiate `rsa_pss_pss_*` from `rsa_pss_rsae_*`, because that check does not have a chain context that lets it inspect the signing certificate's key OID.

RSA-PSS parameter equality is not enforced for certificate signatures:

- `crypto/rsa/rsa_ameth.c:541-588` decodes RSA-PSS signature parameters and applies them to an EVP verification context.
- `providers/implementations/signature/rsa_sig.c:556-598` imports RSA-PSS public-key parameters as restrictions, including `min_saltlen`.
- `providers/implementations/signature/rsa_sig.c:768-789` and `providers/implementations/signature/rsa_sig.c:1739-1744` enforce salt length as a minimum restriction, not byte-for-byte equality with certificate signature parameters.

This matches the runtime behavior: generic verification accepts an RFC 4055-compatible larger salt length, but TLS does not add the stricter RFC 8446 certificate-signature policy.

## Runtime Evidence

The focused test generated two certificate-chain families and exercised both standalone X.509 verification and TLS 1.3 handshakes. The complete test exited with code 0.

### Certificate Evidence

Parameter-mismatch chain:

- `ca_pss.crt`: public-key algorithm `rsassaPss`.
- `ca_pss.crt`: public-key parameters include minimum salt length `32`.
- `ee_mismatch.crt`: certificate signature algorithm `rsassaPss`.
- `ee_mismatch.crt`: certificate signature salt length `0x40`.

OID-family chain:

- `ca_rsa.crt`: public-key algorithm `rsaEncryption`.
- `ee_pss_by_rsa_ca.crt`: certificate signature algorithm `rsassaPss`.
- `ee_pss_by_rsa_ca.crt`: EE public-key algorithm `rsassaPss`.

### Verification Evidence

OpenSSL verification produced:

```text
== verify matching ==
ee_match.crt: OK
rc=0
== verify mismatching ==
ee_mismatch.crt: OK
rc=0
== verify strict mismatching ==
ee_mismatch.crt: OK
rc=0
== verify RSA-CA/PSS-EE ==
ee_pss_by_rsa_ca.crt: OK
rc=0
```

The strict verifier accepts the parameter-mismatching chain and the RSA-CA/PSS-EE chain.

### TLS Handshake Evidence

The TLS 1.3 handshake cases produced:

```text
match rc=0
mismatch rc=0
mismatch_strict rc=0

pss_only_sigalgs_with_rsa_ca_cert_signature rc=0
```

```text
CONNECTION ESTABLISHED
Protocol version: TLSv1.3
Peer certificate: CN=localhost
Signature type: rsa_pss_rsae_sha256
Verification: OK
```

```text
CONNECTION ESTABLISHED
Protocol version: TLSv1.3
Peer certificate: CN=localhost
Signature type: rsa_pss_pss_sha256
Verification: OK
```

The TLS client accepts both non-compliant cases, including the run restricted to `rsa_pss_pss_sha256`.

## Decision Reason

This is a real issue because RFC 8446 applies TLS `SignatureScheme` semantics to certificate signatures and makes RSA/PSS public-key OID family and PSS parameter constraints part of that policy. OpenSSL validates the generic X.509 signature successfully, but the TLS layer does not reject chains whose certificate signatures violate those stricter RFC 8446 constraints.

The issue scope is precise:

- Not failing: CertificateVerify end-entity key OID family validation.
- Failing: certificate-chain signature validation under TLS 1.3 policy, including issuer public-key OID family and RSA-PSS parameter equality/salt-length requirements.

## Remaining Work

No uncertainty remains for the tested certificate-signature paths. A fix design needs to decide where to enforce the TLS-specific policy:

- in TLS chain validation, with access to each issuer certificate's public-key OID and RSA-PSS parameters; or
- through an X.509 verification policy hook that can be enabled specifically for TLS SignatureScheme validation.
