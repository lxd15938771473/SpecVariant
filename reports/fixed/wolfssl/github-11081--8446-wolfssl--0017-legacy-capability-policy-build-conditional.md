# Potential mismatch in remove all RC4 support from the configuration

## Verdict

- Verdict: `suspected_issue`
- Confidence: `high`
- Covered Record IDs: `cand-129f414089b2-baseline`, `cand-17e6872ce8d2-boundary`, `cand-8e65faea6b0b-unknown`, `cand-6e10e91d0075-duplicate`, `cand-f57dcaced164-baseline`, `cand-825ff4ccb0c9-boundary`, `cand-740db5502fef-unknown`, `cand-19fcddf6ae44-duplicate`, `cand-f8862eece440-baseline`, `cand-315bc821d21f-boundary`, `cand-09eb6746727b-unknown`, `cand-3c71c7653f26-duplicate`, `cand-74d1d7ee7960-baseline`, `cand-850a1ae4ee92-boundary`, `cand-a018f14600ab-error-mapping`, `cand-ef0a93ec0cb6-unknown`
- Root Cause Key: `legacy-capability-policy-build-conditional`

## Problem Description

Static triage found a plausible mismatch in wolfSSL's handling of the requirement to remove all RC4 support from the configuration\. This candidate targets the baseline case, and the concern remains unresolved after runtime/source\-backed follow\-up\.

This report deduplicates multiple candidate-level records that resolved to the same root cause.

## Standard Requirement

- Official standard: [RFC 8446](https://www.rfc-editor.org/rfc/rfc8446.html)
- Requirement ID: `req-38a6d8bea3a9af884d81`
- Section: Appendix C Implementation Notes \(lines 7541\-7545\)

> -  Have you ensured that all support for SSL, RC4, EXPORT ciphers,
>       and MD5 (via the "signature_algorithms" extension) is completely
>       removed from all possible configurations that support TLS 1.3 or
>       later, and that attempts to use these obsolete capabilities fail
>       correctly (see Appendix D)?

Interpretation:

Applies a configuration supports TLS 1\.3 or later\. The implementation must remove all RC4 support from the configuration\. It must not enable RC4\.

- Requirement ID: `req-7936db11b9edca04b8ef`
- Section: Appendix C Implementation Notes \(lines 7541\-7545\)

> -  Have you ensured that all support for SSL, RC4, EXPORT ciphers,
>       and MD5 (via the "signature_algorithms" extension) is completely
>       removed from all possible configurations that support TLS 1.3 or
>       later, and that attempts to use these obsolete capabilities fail
>       correctly (see Appendix D)?

Interpretation:

Applies a configuration supports TLS 1\.3 or later\. The implementation must remove all EXPORT\-cipher support from the configuration\. It must not enable EXPORT ciphers\.

- Requirement ID: `req-8f0c1dfbff03440dcf92`
- Section: Appendix C Implementation Notes \(lines 7541\-7545\)

> -  Have you ensured that all support for SSL, RC4, EXPORT ciphers,
>       and MD5 (via the "signature_algorithms" extension) is completely
>       removed from all possible configurations that support TLS 1.3 or
>       later, and that attempts to use these obsolete capabilities fail
>       correctly (see Appendix D)?

Interpretation:

Applies a configuration supports TLS 1\.3 or later\. The implementation must remove all SSL support from the configuration\. It must not enable SSL\.

- Requirement ID: `req-de6b709310a4c63af29a`
- Section: Appendix C Implementation Notes \(lines 7541\-7545\)

> -  Have you ensured that all support for SSL, RC4, EXPORT ciphers,
>       and MD5 (via the "signature_algorithms" extension) is completely
>       removed from all possible configurations that support TLS 1.3 or
>       later, and that attempts to use these obsolete capabilities fail
>       correctly (see Appendix D)?

Interpretation:

Applies a peer attempts to use SSL, RC4, EXPORT ciphers, or MD5 in a TLS 1\.3\-capable configuration\. The implementation must reject the obsolete capability\. On violation it must fail the attempt without activating the obsolete capability\.

## Relevant Source Code

wolfSSL has hardening defaults for obsolete RC4/legacy protocol behavior, but the reviewed tree still retains optional legacy build/configuration paths or broad rejection policy surfaces\.

### `wolfssl/wolfcrypt/settings.h:5365-5369`

```
/* RC4: Per RFC7465 Feb 2015, the cipher suite has been deprecated due to a
 * number of exploits capable of decrypting portions of encrypted messages. */
#ifndef WOLFSSL_ALLOW_RC4
    #undef  NO_RC4
    #define NO_RC4
```

RC4 is disabled by default unless the build explicitly re\-enables it with WOLFSSL\_ALLOW\_RC4\.

### `wolfssl/internal.h:264-272`

```
    #if !defined(NO_RSA) && !defined(NO_RC4) && !defined(WSSL_HARDEN_TLS)
        /* MUST NOT negotiate RC4 cipher suites
         * https://www.rfc-editor.org/rfc/rfc9325#section-4.1 */
        #if defined(WOLFSSL_STATIC_RSA)
            #if !defined(NO_SHA)
                #define BUILD_SSL_RSA_WITH_RC4_128_SHA
            #endif
            #if !defined(NO_MD5)
                #define BUILD_SSL_RSA_WITH_RC4_128_MD5
```

The source tree still contains optional RC4 suite build paths when NO\_RC4 is not defined and hardening is off\.

### `src/ssl.c:3472-3485`

```
    #if defined(WOLFSSL_ALLOW_SSLV3) && !defined(NO_OLD_TLS)
    WOLFSSL_METHOD* wolfSSLv3_client_method(void)
    {
        return wolfSSLv3_client_method_ex(NULL);
    }
    WOLFSSL_METHOD* wolfSSLv3_client_method_ex(void* heap)
    {
        WOLFSSL_METHOD* method =
                              (WOLFSSL_METHOD*) XMALLOC(sizeof(WOLFSSL_METHOD),
                                                     heap, DYNAMIC_TYPE_METHOD);
        (void)heap;
        WOLFSSL_ENTER("wolfSSLv3_client_method_ex");
        if (method)
            InitSSL_Method(method, MakeSSLv3());
```

SSLv3 methods remain available behind WOLFSSL\_ALLOW\_SSLV3 and old\-TLS build options\.

## Runtime Evidence

The runtime pass first completed a normal TLS 1.3 connection as a positive control. It then evaluated the RC4, EXPORT, SSL, and MD5 capability families against the audited source configuration. All four checks completed and returned `legacy_capability_policy_remains_build_or_config_dependent`.

The checks observed that the audited default configuration disables the obsolete capabilities, but the source tree still contains optional legacy build switches or broad configuration-dependent rejection paths. Because no single build matrix exercised every optional configuration, the run did not prove that all TLS 1.3-capable configurations remove every obsolete capability. The runtime evidence therefore supports the retained `suspected_issue` classification but does not confirm a concrete wire-level failure.

## Inconsistency Reason

- The defaults are encouraging, yet the strict no\_issue contract is blocked because the audited target build was not pinned and legacy paths remain present in source\. The source shows hardening defaults for some obsolete features, but still retains optional legacy build/configuration paths or broad rejection policy surfaces that prevent strict static closure\.

## Decision Reason

- Static triage found a plausible mismatch or incompletely closed condition, and the focused follow\-up still left material uncertainty\. The source shows hardening defaults for some obsolete features, but still retains optional legacy build/configuration paths or broad rejection policy surfaces that prevent strict static closure\.

## Remaining Uncertainty

- A build\-pinned check is still needed to show the audited target cannot enable the obsolete capability and rejects it on every relevant path\. The source shows hardening defaults for some obsolete features, but still retains optional legacy build/configuration paths or broad rejection policy surfaces that prevent strict static closure\.
