# Confirmed mismatch in RSA-PSS salt-length enforcement during verification

## Summary

This is a real RFC 8446 compliance issue in the RSA-PSS verification path used by TLS 1.3 and shared with certificate-signature verification.

The final scope is narrower than the original suspicion. The rerun does not show that mbedTLS generates non-standard RSA-PSS salt lengths. The confirmed mismatch is on verification: a verifier reached through `mbedtls_pk_verify_ext(MBEDTLS_PK_SIGALG_RSA_PSS, ...)` accepts an RSA-PSS signature whose salt length does not match the digest output length.

The focused rerun on `2026-08-04` confirmed the decisive behavior with actual execution:

1. A standard SHA-256 RSA-PSS signature with `saltlen=32` is accepted by both `mbedtls_pk_verify_ext(RSA_PSS)` and `psa_verify_hash(PSA_ALG_RSA_PSS)`.
2. A non-standard SHA-256 RSA-PSS signature with `saltlen=2` is rejected by `psa_verify_hash(PSA_ALG_RSA_PSS)`.
3. The same non-standard `saltlen=2` signature is still accepted by `mbedtls_pk_verify_ext(RSA_PSS)`.
4. `psa_verify_hash(PSA_ALG_RSA_PSS_ANY_SALT)` also accepts that non-standard signature.

This proves that the relaxation is introduced by the PK verification wrapper, not by the underlying PSA implementation.

## Standard Requirement

- Primary Section: RFC 8446 Section `4.2.3`

### RFC 8446: RSASSA-PSS RSAE algorithms

Source: [RFC 8446](https://www.rfc-editor.org/rfc/rfc8446.html)

```text
RSASSA-PSS RSAE algorithms:  Indicates a signature algorithm using
   RSASSA-PSS [RFC8017] with mask generation function 1.  The digest
   used in the mask generation function and the digest being signed
   are both the corresponding hash algorithm as defined in [SHS].
   The length of the Salt MUST be equal to the length of the output
   of the digest algorithm.
```

### RFC 8446: RSASSA-PSS PSS algorithms

Source: [RFC 8446](https://www.rfc-editor.org/rfc/rfc8446.html)

```text
RSASSA-PSS PSS algorithms:  Indicates a signature algorithm using
   RSASSA-PSS [RFC8017] with mask generation function 1.  The digest
   used in the mask generation function and the digest being signed
   are both the corresponding hash algorithm as defined in [SHS].
   The length of the Salt MUST be equal to the length of the digest
   algorithm.
```

### Interpretation

In TLS 1.3, the `rsa_pss_rsae_*` and `rsa_pss_pss_*` SignatureScheme values define a specific RSA-PSS parameter profile, not merely a hash family:

- the message hash is fixed by the selected SignatureScheme
- the MGF1 hash is the same digest
- the salt length is fixed to the digest output length

For SHA-256, the digest output length is `32` bytes. A verifier that accepts a signature with `saltlen=2` is accepting a signature outside the TLS-defined RSA-PSS profile and therefore outside the corresponding RFC 8446 SignatureScheme.

## Relevant Source Code

TLS 1.3 verification reaches `mbedtls_pk_verify_ext()`, and the RSA-PSS branch of that function explicitly relaxes salt-length checking to `ANY_SALT`.

### TLS 1.3 uses `mbedtls_pk_verify_ext()` for CertificateVerify

Source: `library/ssl_tls13_generic.c:339-349`

```c
    ret = mbedtls_pk_verify_ext((mbedtls_pk_sigalg_t) sig_alg,
                                &ssl->session_negotiate->peer_cert->pk,
                                md_alg, verify_hash, verify_hash_len,
                                p, signature_len);
```

This is the decisive verifier call for TLS 1.3 `CertificateVerify`.

### The public PK API documentation already states that RSA-PSS verification accepts any salt length

Source: `tf-psa-crypto/include/mbedtls/pk.h:570-579`

```c
 * \note            If \p type is #MBEDTLS_PK_SIGALG_RSA_PSS, then any salt
 *                  length is accepted: #PSA_ALG_RSA_PSS_ANY_SALT is used.
 */
int mbedtls_pk_verify_ext(mbedtls_pk_sigalg_t type,
                          mbedtls_pk_context *ctx, mbedtls_md_type_t md_alg,
                          const unsigned char *hash, size_t hash_len,
                          const unsigned char *sig, size_t sig_len);
```

This note matches the runtime behavior seen in the rerun.

### The RSA-PSS verification implementation hardcodes `PSA_ALG_RSA_PSS_ANY_SALT`

Source: `tf-psa-crypto/extras/pk.c:1248-1257`

```c
    psa_algorithm_t psa_md_alg = mbedtls_md_psa_alg_from_type(md_alg);
    mbedtls_svc_key_id_t key_id = MBEDTLS_SVC_KEY_ID_INIT;
    psa_key_attributes_t attributes = PSA_KEY_ATTRIBUTES_INIT;
    psa_algorithm_t psa_sig_alg = PSA_ALG_RSA_PSS_ANY_SALT(psa_md_alg);

    psa_set_key_type(&attributes, PSA_KEY_TYPE_RSA_PUBLIC_KEY);
    psa_set_key_usage_flags(&attributes, PSA_KEY_USAGE_VERIFY_HASH);
    psa_set_key_algorithm(&attributes, psa_sig_alg);
```

So the verifier is intentionally instantiated with relaxed salt-length semantics.

### The signing path still uses standard-salt RSA-PSS

Source: `tf-psa-crypto/extras/pk.c:1389-1400`

```c
        status = psa_sign_hash(ctx->priv_id, PSA_ALG_RSA_PSS(psa_md_alg),
                               hash, hash_len,
                               sig, sig_size, sig_len);
        if (status == PSA_ERROR_NOT_PERMITTED) {
            status = psa_sign_hash(ctx->priv_id, PSA_ALG_RSA_PSS_ANY_SALT(psa_md_alg),
                                   hash, hash_len,
                                   sig, sig_size, sig_len);
        }
        return PSA_PK_RSA_TO_MBEDTLS_ERR(status);
    }

    return mbedtls_pk_psa_rsa_sign_ext(PSA_ALG_RSA_PSS(psa_md_alg),
```

Current evidence therefore points to a verifier-side issue, not a signing-side issue.

### X.509 parsing records the expected salt length but does not enforce it in the TLS/X.509 verification flow

Source: `library/x509.c:748-759`

```c
        ret = mbedtls_x509_get_rsassa_pss_params(sig_params,
                                                 md_alg,
                                                 &mgf1_hash_id,
                                                 &expected_salt_len);
        if (ret != 0) {
            return ret;
        }
        /* Ensure MGF1 hash alg is the same as the one used to hash the message. */
        if (mgf1_hash_id != *md_alg) {
            return MBEDTLS_ERR_X509_INVALID_ALG;
```

And certificate signature verification later reuses the same PK verifier:

Source: `library/x509_crt.c:2145`

```c
    return mbedtls_pk_verify_ext(child->sig_pk, &parent->pk,
                                 child->sig_md, hash, hash_len,
                                 child->sig.p, child->sig.len);
```

This means the same relaxed RSA-PSS verification semantics also affect certificate-signature verification once parsing succeeds.

## Runtime Evidence



### Test Setup

The rerun generated one RSA key pair and one shared message, then produced two SHA-256 RSA-PSS signatures over the same message:

- control case: `saltlen=32`, which matches the SHA-256 digest output length
- reproducer case: `saltlen=2`, which violates the RFC 8446 requirement for `rsa_pss_*_sha256`

The focused C reproducer links against the current build and verifies each signature with three paths:

1. `mbedtls_pk_verify_ext(MBEDTLS_PK_SIGALG_RSA_PSS, ...)`
2. `psa_verify_hash(..., PSA_ALG_RSA_PSS(PSA_ALG_SHA_256), ...)`
3. `psa_verify_hash(..., PSA_ALG_RSA_PSS_ANY_SALT(PSA_ALG_SHA_256), ...)`

This is sufficient to confirm the bug because the TLS 1.3 `CertificateVerify` path shown above reaches `mbedtls_pk_verify_ext()` directly.

### Control Run: Standard Salt Length

The standard-salt control produced:

```text
mbedtls_pk_verify_ext(RSA_PSS): 0
psa_verify_hash(PSA_ALG_RSA_PSS): PSA_SUCCESS (0)
psa_verify_hash(PSA_ALG_RSA_PSS_ANY_SALT): PSA_SUCCESS (0)
```

This confirms that the standard SHA-256 RSA-PSS case behaves as expected.

### Reproducer Run: Non-standard Salt Length

The non-standard-salt case produced:

```text
mbedtls_pk_verify_ext(RSA_PSS): 0
psa_verify_hash(PSA_ALG_RSA_PSS): PSA_ERROR_INVALID_SIGNATURE (-149)
psa_verify_hash(PSA_ALG_RSA_PSS_ANY_SALT): PSA_SUCCESS (0)
```

This is the decisive runtime result:

- the strict standard-salt PSA verifier rejects the `saltlen=2` signature
- the relaxed `ANY_SALT` verifier accepts it
- `mbedtls_pk_verify_ext(RSA_PSS)` behaves like the relaxed verifier, not like the strict one required by RFC 8446

### Result Interpretation

The rerun removes the key ambiguity from the original report.

The underlying PSA implementation is capable of enforcing the standard RSA-PSS salt-length rule. The mismatch is introduced one layer above, when the PK wrapper unconditionally converts RSA-PSS verification into `PSA_ALG_RSA_PSS_ANY_SALT`.

## Inconsistency Reason

RFC 8446 defines RSA-PSS SignatureScheme values with a fixed salt-length rule tied to the selected digest.

The implementation instead behaves as follows:

1. TLS 1.3 verification computes the transcript hash and delegates RSA-PSS verification to `mbedtls_pk_verify_ext()`.
2. `mbedtls_pk_verify_ext()` does not instantiate a standard RSA-PSS verifier.
3. It instantiates `PSA_ALG_RSA_PSS_ANY_SALT(...)`, which accepts arbitrary salt lengths during verification.
4. As a result, signatures that are outside the TLS-defined `rsa_pss_*` parameter profile are still accepted.

That is inconsistent with RFC 8446's definition of the RSA-PSS SignatureScheme.

## Decision Reason

The previous `suspected_issue` assessment depended on an unverified assumption about lower-layer semantics.

The rerun on `2026-08-04` resolves that uncertainty:

- the RFC text fixes the RSA-PSS salt length for the SignatureScheme
- the public PK API documentation explicitly states that RSA-PSS verification accepts any salt length
- the implementation hardcodes `PSA_ALG_RSA_PSS_ANY_SALT(...)`
- the control run shows standard-salt signatures pass everywhere
- the reproducer run shows a non-standard `saltlen=2` signature is rejected by strict PSA verification but accepted by `mbedtls_pk_verify_ext(RSA_PSS)`

The correct final classification is therefore `confirmed_issue`.

## Remaining Uncertainty

There is no remaining uncertainty for the verifier-side root cause.

This rerun does not claim that the signing path generates non-standard salt lengths. The confirmed problem is specifically that RSA-PSS verification is too permissive for the RFC 8446 SignatureScheme definition.
