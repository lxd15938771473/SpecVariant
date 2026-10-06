# Client sends protocol\_version instead of illegal\_parameter for invalid supported\_versions

## Verdict

- Verdict: `issue_found`
- Confidence: `high`
- Covered Record IDs: `cand-79f259626202-baseline`, `cand-a2f893d0b61b-state-order`, `cand-c3e94511ae23-error-mapping`, `cand-f57565cb0be7-boundary`, `cand-69747f16355d-baseline`, `cand-824670378e68-state-order`, `cand-fbef13bc00c4-error-mapping`, `cand-833517c1757c-boundary`
- Root Cause Key: `supported-versions-alert-mapping`

## Problem Description

When the server selects a TLS 1\.3 supported\_versions value the client did not offer, or a version prior to TLS 1\.3, wolfSSL aborts the handshake but emits alert 70 \(protocol\_version\) instead of the RFC\-required alert 47 \(illegal\_parameter\)\. The current run reproduced both cases with a memio probe and captured the client alert on the wire\.

This report deduplicates multiple candidate-level records that resolved to the same root cause.

## Standard Requirement

- Official standard: [RFC 8446](https://www.rfc-editor.org/rfc/rfc8446.html)
- Requirement ID: `req-6b98e71b3d90b2bbcb78`
- Section: Section 4\.2\.1 Supported Versions \(lines 2188\-2192\)

> If
>    the "supported_versions" extension in the ServerHello contains a
>    version not offered by the client or contains a version prior to
>    TLS 1.3, the client MUST abort the handshake with an
>    "illegal_parameter" alert.

Interpretation:

Condition: ServerHello supported\_versions selects a version the client did not offer\. Forbidden behavior: continue the handshake\. Required error behavior: abort the handshake with an illegal\_parameter alert\.

- Requirement ID: `req-7344344d559ca0018440`
- Section: Section 4\.2\.1 Supported Versions \(lines 2188\-2192\)

> If
>    the "supported_versions" extension in the ServerHello contains a
>    version not offered by the client or contains a version prior to
>    TLS 1.3, the client MUST abort the handshake with an
>    "illegal_parameter" alert.

Interpretation:

Condition: ServerHello supported\_versions selects a version prior to TLS 1\.3\. Forbidden behavior: continue the handshake\. Required error behavior: abort the handshake with an illegal\_parameter alert\.

## Relevant Source Code

An unoffered supported\_versions value is rejected with VERSION\_ERROR, which maps to protocol\_version, not illegal\_parameter\.

A pre\-TLS\-1\.3 supported\_versions value in ServerHello is rejected with VERSION\_ERROR, which maps to protocol\_version, not illegal\_parameter\.

### `src/tls.c:7422-7468`

```
    else if (msgType == server_hello || msgType == hello_retry_request) {
        /* Must contain one version. */
        if (length != OPAQUE16_LEN)
            return BUFFER_ERROR;

        major = input[0];
        minor = input[OPAQUE8_LEN];

        if (major != ssl->ctx->method->version.major) {
            WOLFSSL_ERROR_VERBOSE(VERSION_ERROR);
            return VERSION_ERROR;
        }

        /* Can't downgrade with this extension below TLS v1.3. */
        if (versionIsLesser(isDtls, minor, tls13minor)) {
            WOLFSSL_ERROR_VERBOSE(VERSION_ERROR);
            return VERSION_ERROR;
        }

        /* Version is TLS v1.2 to handle downgrading from TLS v1.3+. */
        if (ssl->options.downgrade && ssl->version.minor == tls12minor) {
            /* Set minor version back to TLS v1.3+ */
            pv->minor = ssl->ctx->method->version.minor;
        }

        /* No upgrade allowed. */
        if (versionIsLesser(isDtls, ssl->version.minor, minor)) {
            WOLFSSL_ERROR_VERBOSE(VERSION_ERROR);
            return VERSION_ERROR;
        }

        /* Check downgrade. */
        if (versionIsGreater(isDtls, ssl->version.minor, minor)) {
            if (!ssl->options.downgrade) {
                WOLFSSL_ERROR_VERBOSE(VERSION_ERROR);
                return VERSION_ERROR;
            }

            if (versionIsLesser(
                    isDtls, minor, ssl->options.minDowngrade)) {
                WOLFSSL_ERROR_VERBOSE(VERSION_ERROR);
                return VERSION_ERROR;
            }

            /* Downgrade the version. */
            pv->minor = minor;
        }
```

ServerHello/HRR supported\_versions parse rejects pre\-TLS\-1\.3 and unoffered upgrades by returning VERSION\_ERROR\.

### `src/internal.c:37153-37179`

```
    int TranslateErrorToAlert(int err)
    {
        switch (err) {
            case WC_NO_ERR_TRACE(BUFFER_ERROR):
                return decode_error;
            case WC_NO_ERR_TRACE(EXT_NOT_ALLOWED):
            case WC_NO_ERR_TRACE(PEER_KEY_ERROR):
            case WC_NO_ERR_TRACE(ECC_PEERKEY_ERROR):
            case WC_NO_ERR_TRACE(BAD_KEY_SHARE_DATA):
            case WC_NO_ERR_TRACE(PSK_KEY_ERROR):
            case WC_NO_ERR_TRACE(INVALID_PARAMETER):
            case WC_NO_ERR_TRACE(HRR_COOKIE_ERROR):
            case WC_NO_ERR_TRACE(BAD_BINDER):
            case WC_NO_ERR_TRACE(DUPLICATE_TLS_EXT_E):
                return illegal_parameter;
            case WC_NO_ERR_TRACE(INCOMPLETE_DATA):
                return missing_extension;
            case WC_NO_ERR_TRACE(MATCH_SUITE_ERROR):
            case WC_NO_ERR_TRACE(MISSING_HANDSHAKE_DATA):
            case WC_NO_ERR_TRACE(PSK_MISSING_ERROR):
                return handshake_failure;
            case WC_NO_ERR_TRACE(VERSION_ERROR):
                return wolfssl_alert_protocol_version;
            case WC_NO_ERR_TRACE(BAD_CERTIFICATE_STATUS_ERROR):
                return bad_certificate_status_response;
            case WC_NO_ERR_TRACE(OUT_OF_ORDER_E):
                return unexpected_message;
```

VERSION\_ERROR maps to protocol\_version; EXT\_NOT\_ALLOWED and DUPLICATE\_TLS\_EXT\_E map to illegal\_parameter\.

## Runtime Evidence

Runtime validation was checked twice and both runs agreed.

### First run (`2026-07-30`)

- Status: `passed`
- Positive control: `passed`
- Exit code: `0`

The probe reported `positive_control_ok=true`, showing that the baseline TLS 1.3 in-memory handshake completed before any mutation was applied.

For the unoffered-version reproducer, the probe mutated the ServerHello `supported_versions` value to `0x7A7A` and then observed:

- `setup_ok=true`
- `mutation_applied=true`
- `handshake_ret=-1`
- `handshake_err=-326`
- `alert_desc=70`
- `alert_level=2`
- `alert_record_found=true`
- `alert_record_hex=15030300020246`

For the pre-TLS-1.3 reproducer, the probe mutated the ServerHello `supported_versions` value to `0x0303` and observed the same result values:

- `setup_ok=true`
- `mutation_applied=true`
- `handshake_ret=-1`
- `handshake_err=-326`
- `alert_desc=70`
- `alert_level=2`
- `alert_record_found=true`
- `alert_record_hex=15030300020246`

### Independent rerun (`2026-08-03`)

- Status: `passed`
- Positive control: `passed`
- Exit code: `0`

On August 3, 2026, an independent rerun rebuilt the same in-memory probe against the current wolfSSL source and library, then executed it again. It reproduced the first run for both mutations: baseline handshake success, followed by `handshake_err=-326` and transmitted alert `70` for both `0x7A7A` and `0x0303`.

### Execution note

The probe must be run from the `wolfssl-master` repository root so that `test_memio_setup()` can resolve the default `./certs/*` test certificates. Running the executable from another directory causes setup to fail before the mutation step and does not invalidate the successful runs above.

### Interpretation

The runtime evidence shows that wolfSSL does reject both malformed ServerHello `supported_versions` values, but in both cases it sends fatal alert description `70` (`protocol_version`) on the wire as record `15 03 03 00 02 02 46`. RFC 8446 Section 4.2.1 requires fatal alert `47` (`illegal_parameter`) for these exact conditions.

## Inconsistency Reason

- wolfSSL aborts these invalid ServerHello supported\_versions cases with protocol\_version instead of the RFC\-required illegal\_parameter alert\.

## Decision Reason

- The static parser path returns VERSION\_ERROR for these supported\_versions violations, and the current memio probe confirmed that wolfSSL emits alert 70 \(protocol\_version\) on the wire instead of the RFC\-required illegal\_parameter\. That is a complete proof of the alert\-mapping defect\.
