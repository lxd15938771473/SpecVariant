# Signature encoding semantics remain unresolved


## Problem Description

These candidates target ECDSA/EdDSA signature representation semantics that the stock runtime families did not inspect at the raw encoding level\.

This report deduplicates multiple candidate-level records that resolved to the same root cause.

## Standard Requirement
- Section: [RFC 8446, Section 4.2.3](https://www.rfc-editor.org/rfc/rfc8446.html#section-4.2.3)

>       defined in [SHS].  The signature is represented as a DER-encoded
>       [X690] ECDSA-Sig-Value structure.

Interpretation:

Condition: an ECDSA signature scheme is used\. Required behavior: represent the signature as a DER\-encoded ECDSA\-Sig\-Value structure\. Forbidden behavior: use a non\-DER or non\-ECDSA\-Sig\-Value representation\.

- Requirement ID: `req-a91da87994265d75d3e5`
- Section: [RFC 8446, Section 4.2.3](https://www.rfc-editor.org/rfc/rfc8446.html#section-4.2.3)

>       defined in [RFC8032] or its successors.  Note that these
>       correspond to the "PureEdDSA" algorithms and not the "prehash"
>       variants.

Interpretation:

Condition: an EdDSA SignatureScheme defined here is used\. Required behavior: use the PureEdDSA algorithm variant\. Forbidden behavior: use a prehash EdDSA variant\.

## Relevant Source Code

OpenSSL has DER ECDSA\-Sig\-Value ASN\.1 helpers, but this batch did not trace the exact EVP handshake\-signature encoding path far enough to terminate the requirement statically\.

The code base clearly supports Ed25519/Ed448 with absent parameters, but this batch did not close the TLS handshake path tightly enough to prove the PureEdDSA\-vs\-prehash distinction for every reachable use\.

### `crypto/asn1_dsa.c:10-22`

```
/*
 * A simple ASN.1 DER encoder/decoder for DSA-Sig-Value and ECDSA-Sig-Value.
 *
 * DSA-Sig-Value ::= SEQUENCE {
 *  r  INTEGER,
 *  s  INTEGER
 * }
 *
 * ECDSA-Sig-Value ::= SEQUENCE {
 *  r  INTEGER,
 *  s  INTEGER
 * }
 */
```

OpenSSL documents and implements ECDSA signatures as DER\-encoded ECDSA\-Sig\-Value structures in its ASN\.1 DSA/ECDSA helpers\.

### `crypto/ec/ecx_meth.c:550-569`

```
    /* Sanity check: make sure it is ED25519/ED448 with absent parameters */
    X509_ALGOR_get0(&obj, &ptype, NULL, sigalg);
    nid = OBJ_obj2nid(obj);
    if ((nid != NID_ED25519 && nid != NID_ED448) || ptype != V_ASN1_UNDEF) {
        ERR_raise(ERR_LIB_EC, EC_R_INVALID_ENCODING);
        return 0;
    }

    if (!EVP_DigestVerifyInit(ctx, NULL, NULL, NULL, pkey))
        return 0;

    return 2;
}

static int ecd_item_sign(X509_ALGOR *alg1, X509_ALGOR *alg2, int nid)
{
    /* Note that X509_ALGOR_set0(..., ..., V_ASN1_UNDEF, ...) cannot fail */
    /* Set algorithms identifiers */
    (void)X509_ALGOR_set0(alg1, OBJ_nid2obj(nid), V_ASN1_UNDEF, NULL);
    if (alg2 != NULL)
```

Ed25519/Ed448 algorithm identifiers are required to have absent parameters, matching PureEdDSA\-style AlgorithmIdentifier handling\.

## Runtime Evidence

The native `70-test_sslsigalgs.t` recipe was run twice against the inspected build: once as a control and once after the focused source review. Both runs exited with code 0 and all TAP checks passed. The recipe exercised TLS 1.2 and TLS 1.3 `signature_algorithms`, legacy compatibility, and `signature_algorithms_cert` behavior.

The observed pass result does not resolve the candidate. Those tests validate algorithm negotiation and policy, but they do not inspect the raw handshake signature bytes to distinguish DER-encoded ECDSA from a non-DER representation or PureEdDSA from a prehash variant. No runtime claim beyond that coverage boundary is made here.

## Inconsistency Reason

- The current runtime evidence does not decode the wire signature objects needed to resolve the targeted representation requirements\.

## Decision Reason

- The native signature\-algorithm recipes passed, but they validate scheme selection rather than raw DER or PureEdDSA encoding details\.

## Remaining Uncertainty

- A small wire\-signature inspector is still needed to parse the emitted signature bytes and check the exact representation rule\.
