# TLS 1.3 cookie extension is absent in the audited default build and only available behind build/runtime gates

## Verdict

- Verdict: `issue_found`
- Confidence: `high`
- Covered Record IDs: `cand-fd5aabc91cb9-baseline`, `cand-ff039c484ae2-boundary`, `cand-b7259988e55d-unknown`, `cand-7aa1b10d8837-duplicate`
- Root Cause Key: `tls13-cookie-extension-feature-gated`

## Executive Summary

The earlier report stopped at a source-backed suspicion. A focused runtime recheck on August 3, 2026 resolves that uncertainty.

For the audited default wolfSSL TLS 1.3 build, the `cookie` extension is not actually implemented on the HelloRetryRequest path: a forced-HRR probe reached a real `HelloRetryRequest`, but the HRR carried no `cookie` extension. The source tree does contain working HRR-cookie support, but that support is guarded by `WOLFSSL_SEND_HRR_COOKIE`, requires a build configured with `WOLFSSL_HRR_COOKIE=yes`, and still requires the server to opt in at runtime with `wolfSSL_send_hrr_cookie()`.

So this is a real issue for the audited default build, not a proof gap. The implementation exists in the source tree, but only behind build/runtime gates rather than as an unconditional TLS 1.3 capability in the checked target.

## Standard Requirement

- Requirement ID: `req-6fc7f85d661dea82e316`
- RFC: [RFC 8446](https://www.rfc-editor.org/rfc/rfc8446.html)
- Section: `9.2 Mandatory-to-Implement Extensions`
- Relevant lines: `5715-5721`

Standard basis:

> In the absence of an application profile standard specifying
>    otherwise, a TLS-compliant application MUST implement the following
>    TLS extensions:
>
>    -  Supported Versions ("supported_versions"; Section 4.2.1)
>
>    -  Cookie ("cookie"; Section 4.2.2)

See [RFC 8446 Section 9.2](https://www.rfc-editor.org/rfc/rfc8446.html#section-9.2).

Interpretation:

- In the audited target, absent an application-profile exception, TLS 1.3 support must include the `cookie` extension capability.
- Because RFC 8446 Section 4.2.2 defines `cookie` for the HelloRetryRequest / second-ClientHello flow, a valid way to test implementation is to force a real HRR and observe whether the stack can actually produce and process that extension.

## Relevant Source Code

The source tree itself already shows that HRR-cookie support is not unconditional.

### 1. wolfSSL documents HRR cookie support as default-off

`src/tls13.c:38` explicitly describes the feature gate:

```c
 * WOLFSSL_SEND_HRR_COOKIE:  Send cookie in HelloRetryRequest     default: off
```

### 2. TLS 1.3 ClientHello extension construction only enables `cookie` under `WOLFSSL_SEND_HRR_COOKIE`

`src/tls.c:17485` and `src/tls.c:17720` only turn on `TLSX_COOKIE` when the macro is compiled in:

```c
#ifdef WOLFSSL_SEND_HRR_COOKIE
    TURN_ON(semaphore, TLSX_ToSemaphore(TLSX_COOKIE));
#endif
```

That means the TLS 1.3 request-size and request-write paths do not treat `cookie` as an unconditional extension in the audited build.

### 3. The server-side runtime opt-in API itself only exists inside the same macro gate

`src/tls13.c:14865` defines `wolfSSL_send_hrr_cookie()` only under `WOLFSSL_SEND_HRR_COOKIE`, and the function merely enables the behavior for a particular `WOLFSSL*`:

```c
int wolfSSL_send_hrr_cookie(WOLFSSL* ssl, const unsigned char* secret,
                            unsigned int secretSz)
{
    ...
    ssl->options.sendCookie = 1;
    ...
}
```

So even a build that compiles the code still needs explicit runtime opt-in before a server will emit an HRR `cookie`.

## Runtime Recheck

### Recheck goal

Resolve the old uncertainty by separating three questions:

1. Does the audited default build emit a real HelloRetryRequest without `cookie`?
2. Does a build compiled with `WOLFSSL_HRR_COOKIE=yes` still omit `cookie` unless explicitly enabled at runtime?
3. If explicitly enabled, does the rebuilt path actually emit `cookie` and pass the existing bad-cookie regression test?

### Recheck method

The test used a native in-memory TLS 1.3 client/server probe in three configurations: the audited default build, a build configured with `WOLFSSL_HRR_COOKIE=yes` without runtime opt-in, and the same enabled build after calling `wolfSSL_send_hrr_cookie()`. It forced HelloRetryRequest by sending the initial ClientHello without a key share, parsed the first server handshake message, and checked for extension type `0x002c` (`cookie`). The enabled configuration was also exercised with the existing bad-cookie regression case.

The probe forces HRR by making the client send a TLS 1.3 ClientHello with no key shares. It then inspects the first server flight and reports whether that flight is really a HelloRetryRequest and whether the HRR carries extension type `0x002c` (`cookie`).

### 1. Audited default build: real HRR, no `cookie`

Build-side macro evidence for the audited default build:

- `wolfssl/options.h:345` leaves `WOLFSSL_SEND_HRR_COOKIE` undefined.
- `CMakeCache.txt:740` records `WOLFSSL_HRR_COOKIE:STRING=undefined`.

Observed runtime output:

```text
compile_macro=off
server_first_message_is_hrr=1
cookie_present=0
```

The full log also shows the handshake really did reach the HRR point:

```text
no_key_shares_ret=1
client_ch1 ret=-1 err=2 s_msg_count=1 c_msg_count=0
server_hrr ret=-1 err=2 s_msg_count=0 c_msg_count=1
```

This is the decisive runtime confirmation that the audited default build can produce a real HelloRetryRequest but does not implement the `cookie` extension there.

### 2. Rebuilt `WOLFSSL_HRR_COOKIE=yes` build: still no `cookie` without runtime opt-in

The separate recheck build was configured with `-DWOLFSSL_TLS13=yes -DWOLFSSL_HRR_COOKIE=yes`. Configuration completed successfully, and a preprocessor check reported `WOLFSSL_SEND_HRR_COOKIE` as defined before the probe was run.

When the same probe runs against that rebuilt tree without calling `wolfSSL_send_hrr_cookie()`, the output is:

```text
compile_macro=on
enable_cookie_request=0
server_first_message_is_hrr=1
cookie_present=0
```

So compiling the feature in is still not enough by itself. The server must also enable it per-connection.

### 3. Rebuilt `WOLFSSL_HRR_COOKIE=yes` build with runtime opt-in: `cookie` appears

When the probe calls `wolfSSL_send_hrr_cookie()` before the handshake, the same rebuilt tree produces:

```text
send_hrr_cookie_ret=1
compile_macro=on
enable_cookie_request=1
server_first_message_is_hrr=1
cookie_present=1
cookie_ext_len=87
cookie_vector_len=85
```

This shows that the source tree does have a working HRR-cookie implementation, but only when both of these conditions hold:

- the build is compiled with `WOLFSSL_HRR_COOKIE=yes`, and
- the server explicitly calls `wolfSSL_send_hrr_cookie()`.

### 4. Existing bad-cookie regression test passes in the enabled build

The rebuilt enabled tree also passes the existing unit test `test_tls13_hrr_bad_cookie`:

```text
1528: test_tls13_hrr_bad_cookie : passed
Failed/Skipped/Passed/All: 0/0/1/1
```

That matters because it rules out a weaker alternative explanation such as "the feature exists only as dead code". Once compiled in and enabled, the path is alive and the implementation rejects corrupted HRR cookies.

## Why This Is a Real Issue

This is not just a documentation mismatch or a source-only caution.

The runtime recheck establishes all of the following:

- The audited default build really reaches a TLS 1.3 HelloRetryRequest path.
- In that audited build, the HRR does not contain `cookie`.
- The absence is explained by a real build/runtime gate, not by the absence of HRR traffic.
- A separate build can emit `cookie`, but only when explicitly rebuilt and opt-in enabled.

That means the checked default target does not satisfy the mandatory-to-implement `cookie` extension requirement as audited here. The problem is build/configuration-scoped, but it is still real for the audited default build.

## Decision Reason

- Source inspection showed the `cookie` extension was controlled by `WOLFSSL_SEND_HRR_COOKIE`.
- The previous report could not tell whether that was only a source-level appearance or an actual capability gap in the audited target.
- The new forced-HRR runtime probe resolves that uncertainty: the audited default build produces a real HRR with no `cookie`, while a separate `WOLFSSL_HRR_COOKIE=yes` build can produce `cookie` only after explicit runtime opt-in.

Therefore this root cause should no longer remain `suspected_issue`. It is a confirmed `issue_found` for the audited default wolfSSL TLS 1.3 build.

## Scope and Remaining Uncertainty

Confirmed scope:

- This report is runtime-confirmed for the audited default wolfSSL TLS 1.3 example build.
- The confirmed mismatch is that this audited build does not implement the TLS 1.3 `cookie` extension capability on the HRR path.

Not claimed here:

- This report does not claim that every possible wolfSSL build is missing the feature.
- The recheck directly shows the opposite: a separately rebuilt tree with `WOLFSSL_HRR_COOKIE=yes` and explicit `wolfSSL_send_hrr_cookie()` opt-in does implement and exercise the feature.

Remaining uncertainty:

- No material uncertainty remains for the audited default build.
