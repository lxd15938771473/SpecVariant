# Client omits the HelloRetryRequest cookie in the retry ClientHello in the default build

## Verdict

- Verdict: `issue_found`
- Confidence: `high`
- Covered Record IDs: `cand-6444dbfc13b3-baseline`, `cand-8895879a1d7b-unknown`, `cand-d7774aa94d4d-missing`, `cand-aebf5ba41be2-duplicate`, `cand-70fc3b8e18da-baseline`, `cand-bcac881a35cb-state-order`, `cand-e4df6e3080c5-unknown`, `cand-ed5f617b42cd-duplicate`
- Root Cause Key: `hrr-cookie-default-build-omission`

## Problem Description

In the default wolfSSL client build where HRR cookie support is not compiled in, the client accepts a HelloRetryRequest that carries a cookie and requests a changed key\_share, but the second ClientHello omits the cookie extension\. A control build with the HRR cookie macro enabled echoes the same cookie bytes, so the mismatch is real for the default build rather than a harness artifact\.

This report deduplicates multiple candidate-level records that resolved to the same root cause.

## Standard Requirement

- Official standard: [RFC 8446](https://www.rfc-editor.org/rfc/rfc8446.html)
- Requirement ID: `req-4df2010c9fbdb7400a50`
- Section: Section 4\.1\.2 Client Hello \(lines 1494\-1495\)

> Including a "cookie" extension if one was provided in the
>       HelloRetryRequest.

Interpretation:

Condition: HelloRetryRequest provides a cookie extension\. Required behavior: include a cookie extension in the retry ClientHello\. Forbidden behavior: omit the provided cookie from the retry ClientHello\.

- Requirement ID: `req-4053eb9d1b667f31bfc4`
- Section: Section 4\.2\.2 Cookie \(lines 2217\-2219\)

> When sending the new ClientHello, the client MUST copy
>    the contents of the extension received in the HelloRetryRequest into
>    a "cookie" extension in the new ClientHello.

Interpretation:

Condition: a client sends a new ClientHello after receiving a cookie extension in HelloRetryRequest\. Required behavior: copy the received cookie extension contents unchanged into a cookie extension in the new ClientHello\. Forbidden behavior: omit or alter the received cookie contents\.

## Relevant Source Code

Default/no\-macro source path omits HRR cookie echo support\.

Retry cookie handling exists only behind the default\-off HRR cookie macro\.

### `src/tls.c:7517-7696`

```
static void TLSX_Cookie_FreeAll(Cookie* cookie, void* heap)
{
    (void)heap;

    XFREE(cookie, heap, DYNAMIC_TYPE_TLSX);
}

/* Get the size of the encoded Cookie extension.
 * In messages: ClientHello and HelloRetryRequest.
 *
 * cookie   The cookie to write.
 * msgType  The type of the message this extension is being written into.
 * returns the number of bytes of the encoded Cookie extension.
 */
static int TLSX_Cookie_GetSize(Cookie* cookie, byte msgType, word16* pSz)
{
    if (msgType == client_hello || msgType == hello_retry_request) {
        *pSz += OPAQUE16_LEN + cookie->len;
    }
    else {
        WOLFSSL_ERROR_VERBOSE(SANITY_MSG_E);
        return SANITY_MSG_E;
    }
    return 0;
}

/* Writes the Cookie extension into the output buffer.
 * Assumes that the the output buffer is big enough to hold data.
 * In messages: ClientHello and HelloRetryRequest.
 *
 * cookie   The cookie to write.
 * output   The buffer to write into.
 * msgType  The type of the message this extension is being written into.
 * returns the number of bytes written into the buffer.
 */
static int TLSX_Cookie_Write(Cookie* cookie, byte* output, byte msgType,
                             word16* pSz)
{
    if (msgType == client_hello || msgType == hello_retry_request) {
        c16toa(cookie->len, output);
        output += OPAQUE16_LEN;
        XMEMCPY(output, cookie->data, cookie->len);
        *pSz += OPAQUE16_LEN + cookie->len;
    }
    else {
        WOLFSSL_ERROR_VERBOSE(SANITY_MSG_E);
        return SANITY_MSG_E;
    }
    return 0;
}

/* Parse the Cookie extension.
 * In messages: ClientHello and HelloRetryRequest.
 *
 * ssl      The SSL/TLS object.
 * input    The extension data.
 * length   The length of the extension data.
 * msgType  The type of the message this extension is being parsed from.
 * returns 0 on success and other values indicate failure.
 */
static int TLSX_Cookie_Parse(WOLFSSL* ssl, const byte* input, word16 length,
                             byte msgType)
{
    word16  len;
    word16  idx = 0;
    TLSX*   extension;
    Cookie* cookie;

    if (msgType != client_hello && msgType != hello_retry_request) {
        WOLFSSL_ERROR_VERBOSE(SANITY_MSG_E);
        return SANITY_MSG_E;
    }

    /* Message contains length and Cookie which must be at least one byte
     * in length.
     */
    if (length < OPAQUE16_LEN + 1)
        return BUFFER_E;
    ato16(input + idx, &len);
    idx += OPAQUE16_LEN;
    if (length - idx != len)
        return BUFFER_E;

    if (msgType == hello_retry_request) {
        ssl->options.hrrSentCookie = 1;
        return TLSX_Cookie_Use(ssl, input + idx, len, NULL, 0, 1,
                               &ssl->extensions);
    }

    /* client_hello */
    extension = TLSX_Find(ssl->extensions, TLSX_COOKIE);
    if (extension == NULL) {
#ifdef WOLFSSL_DTLS13
        if (ssl->options.dtls && IsAtLeastTLSv1_3(ssl->version))
            /* Allow a cookie extension with DTLS 1.3 because it is possible
             * that a different SSL instance sent the cookie but we are now
             * receiving it. */
            return TLSX_Cookie_Use(ssl, input + idx, len, NULL, 0, 0,
                                   &ssl->extensions);
        else
#endif
        {
            WOLFSSL_ERROR_VERBOSE(HRR_COOKIE_ERROR);
            return HRR_COOKIE_ERROR;
        }
    }

    cookie = (Cookie*)extension->data;
    if (cookie->len != len || XMEMCMP(cookie->data, input + idx, len) != 0) {
        WOLFSSL_ERROR_VERBOSE(HRR_COOKIE_ERROR);
        return HRR_COOKIE_ERROR;
    }

    /* Request seen. */
    extension->resp = 0;

    return 0;
}

/* Use the data to create a new Cookie object in the extensions.
 *
 * ssl    SSL/TLS object.
 * data   Cookie data.
 * len    Length of cookie data in bytes.
 * mac    MAC data.
 * macSz  Length of MAC data in bytes.
 * resp   Indicates the extension will go into a response (HelloRetryRequest).
 * returns 0 on success and other values indicate failure.
 */
int TLSX_Cookie_Use(const WOLFSSL* ssl, const byte* data, word16 len, byte* mac,
                    byte macSz, int resp, TLSX** exts)
{
    int     ret = 0;
    TLSX*   extension;
    Cookie* cookie;

    /* Find the cookie extension if it exists. */
    extension = TLSX_Find(*exts, TLSX_COOKIE);
    if (extension == NULL) {
        /* Push new cookie extension. */
        ret = TLSX_Push(exts, TLSX_COOKIE, NULL, ssl->heap);
        if (ret != 0)
            return ret;

        extension = TLSX_Find(*exts, TLSX_COOKIE);
        if (extension == NULL)
            return MEMORY_E;
    }

    cookie = (Cookie*)XMALLOC(sizeof(Cookie) + len + macSz, ssl->heap,
                              DYNAMIC_TYPE_TLSX);
    if (cookie == NULL)
        return MEMORY_E;

    cookie->len = len + macSz;
    XMEMCPY(cookie->data, data, len);
    if (mac != NULL)
        XMEMCPY(cookie->data + len, mac, macSz);

    XFREE(extension->data, ssl->heap, DYNAMIC_TYPE_TLSX);

    extension->data = (void*)cookie;
    extension->resp = (byte)resp;

    return 0;
}

#define CKE_FREE_ALL  TLSX_Cookie_FreeAll
#define CKE_GET_SIZE  TLSX_Cookie_GetSize
#define CKE_WRITE     TLSX_Cookie_Write
#define CKE_PARSE     TLSX_Cookie_Parse

#else

#define CKE_FREE_ALL(a, b)    WC_DO_NOTHING
#define CKE_GET_SIZE(a, b, c) 0
#define CKE_WRITE(a, b, c, d) 0
#define CKE_PARSE(a, b, c, d) 0

#endif
```

Cookie support is macro\-gated; without the macro, parse/write hooks are no\-ops\.

### `src/tls.c:17471-17487`

```
#if defined(WOLFSSL_TLS13)
        if (!IsAtLeastTLSv1_2(ssl)) {
            TURN_ON(semaphore, TLSX_ToSemaphore(TLSX_SUPPORTED_VERSIONS));
        }
    #if !defined(WOLFSSL_NO_TLS12) || !defined(NO_OLD_TLS)
        if (!IsAtLeastTLSv1_3(ssl->version)) {
            TURN_ON(semaphore, TLSX_ToSemaphore(TLSX_KEY_SHARE));
        #if defined(HAVE_SESSION_TICKET) || !defined(NO_PSK)
            TURN_ON(semaphore, TLSX_ToSemaphore(TLSX_PRE_SHARED_KEY));
            TURN_ON(semaphore, TLSX_ToSemaphore(TLSX_PSK_KEY_EXCHANGE_MODES));
        #endif
        #ifdef WOLFSSL_EARLY_DATA
            TURN_ON(semaphore, TLSX_ToSemaphore(TLSX_EARLY_DATA));
        #endif
        #ifdef WOLFSSL_SEND_HRR_COOKIE
            TURN_ON(semaphore, TLSX_ToSemaphore(TLSX_COOKIE));
        #endif
```

TLS 1\.3 ClientHello only enables cookie extension when the macro is on\.

### `src/tls13.c:6070-6081`

```
        /* Check if the HRR contained a cookie or a keyshare */
        if (!ssl->options.hrrSentKeyShare
#ifdef WOLFSSL_SEND_HRR_COOKIE
                && !ssl->options.hrrSentCookie
#endif
                ) {
            SendAlert(ssl, alert_fatal, illegal_parameter);
            return EXT_MISSING;
        }

        ssl->options.tls1_3 = 1;
        ssl->options.serverState = SERVER_HELLO_RETRY_REQUEST_COMPLETE;
```

HRR path can proceed on key\_share change even when cookie support is compiled out\.

### `src/tls13.c:37-39`

```
 *                            Sends ChangeCipherSpec and includes session id
 * WOLFSSL_SEND_HRR_COOKIE:  Send cookie in HelloRetryRequest     default: off
 *                            for stateless ClientHello tracking
```

HRR cookie support is compile\-gated and default\-off\.

### `src/tls.c:7577-7628`

```
static int TLSX_Cookie_Parse(WOLFSSL* ssl, const byte* input, word16 length,
                             byte msgType)
{
    word16  len;
    word16  idx = 0;
    TLSX*   extension;
    Cookie* cookie;

    if (msgType != client_hello && msgType != hello_retry_request) {
        WOLFSSL_ERROR_VERBOSE(SANITY_MSG_E);
        return SANITY_MSG_E;
    }

    /* Message contains length and Cookie which must be at least one byte
     * in length.
     */
    if (length < OPAQUE16_LEN + 1)
        return BUFFER_E;
    ato16(input + idx, &len);
    idx += OPAQUE16_LEN;
    if (length - idx != len)
        return BUFFER_E;

    if (msgType == hello_retry_request) {
        ssl->options.hrrSentCookie = 1;
        return TLSX_Cookie_Use(ssl, input + idx, len, NULL, 0, 1,
                               &ssl->extensions);
    }

    /* client_hello */
    extension = TLSX_Find(ssl->extensions, TLSX_COOKIE);
    if (extension == NULL) {
#ifdef WOLFSSL_DTLS13
        if (ssl->options.dtls && IsAtLeastTLSv1_3(ssl->version))
            /* Allow a cookie extension with DTLS 1.3 because it is possible
             * that a different SSL instance sent the cookie but we are now
             * receiving it. */
            return TLSX_Cookie_Use(ssl, input + idx, len, NULL, 0, 0,
                                   &ssl->extensions);
        else
#endif
        {
            WOLFSSL_ERROR_VERBOSE(HRR_COOKIE_ERROR);
            return HRR_COOKIE_ERROR;
        }
    }

    cookie = (Cookie*)extension->data;
    if (cookie->len != len || XMEMCMP(cookie->data, input + idx, len) != 0) {
        WOLFSSL_ERROR_VERBOSE(HRR_COOKIE_ERROR);
        return HRR_COOKIE_ERROR;
    }
```

When compiled in, the client stores the HRR cookie and later requires an exact echo\.

## Runtime Evidence

### Round 1

- Status: `passed`
- Positive control: `passed`
- Reproducer: `passed`

The current run used an HRR-cookie probe with TLS-Attacker. In the default build, TLS-Attacker captured two ClientHello messages after a cookie-bearing HelloRetryRequest: the first `key_share` group was `00 1D` and the second changed to `00 17`, proving that the client accepted the HRR path. The second ClientHello still reported `cookie_present=false`, and its extension bytes contained neither type `00 2C` nor the expected cookie bytes `AA BB CC DD 01 02 03 04`. The positive control used the HRR-cookie-enabled client build with a SECP384R1 HRR request. TLS-Attacker again captured two ClientHello messages, and the second ClientHello reported `cookie_present=true` with extension bytes beginning `00 2C 00 0A 00 08 AA BB CC DD 01 02 03 04`.

The probe completed with exit code `0`; both the default-build reproduction and macro-enabled positive control produced the expected captures described above.

## Inconsistency Reason

- The default wolfSSL client build can send the second ClientHello after a cookie\-bearing HelloRetryRequest without including the required cookie extension, even though a build with HRR cookie support enabled echoes the same cookie\.

## Decision Reason

- The static code path shows HRR cookie parse and write support gated behind a default\-off macro, and the current run reproduced the externally visible consequence in the default build while a macro\-enabled control echoed the same cookie correctly\. That combination is enough to confirm the mismatch\.
