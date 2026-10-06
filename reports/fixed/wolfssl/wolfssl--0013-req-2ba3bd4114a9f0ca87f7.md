# wolfSSL old-TLS builds can negotiate TLS 1.1

## Summary

This is a real issue, but the affected scope must be precise. wolfSSL's default build disables TLS 1.0 and TLS 1.1, so the default build is not affected. When the build explicitly enables `WOLFSSL_OLD_TLS=yes`, TLS 1.1 client and server methods are compiled in and can complete a real TCP handshake and application-data exchange using TLS 1.1.

## Standard Requirement

Official standard links:

- [RFC 9846 Appendix E.5, Deprecated and Removed Features](https://datatracker.ietf.org/doc/html/rfc9846#appendix-E.5)
- [RFC 9846 Section 1.2, Major Differences from TLS 1.3](https://datatracker.ietf.org/doc/html/rfc9846#section-1.2)
- [RFC 9846 Section 4.2.3, ServerHello](https://datatracker.ietf.org/doc/html/rfc9846#section-4.2.3)
- [RFC 8446, The Transport Layer Security Protocol Version 1.3](https://datatracker.ietf.org/doc/html/rfc8446)
- [RFC 8996, Deprecating TLS 1.0 and TLS 1.1](https://datatracker.ietf.org/doc/html/rfc8996)
- [RFC 9325 Section 3.1.1, SSL/TLS Protocol Versions](https://datatracker.ietf.org/doc/html/rfc9325#section-3.1.1)

RFC 9846 Appendix E.5 is the direct requirement: SSL 2.0, SSL 3.0, TLS 1.0, and TLS 1.1 are considered insufficient and must not be negotiated for any reason. Therefore the correct requirement meaning is `Forbidden: Negotiate TLS 1.1`. Any earlier candidate phrased as `Required: Negotiate TLS 1.1` is an extraction-direction error and cannot be used as the standard interpretation.

The surrounding standard context supports the same conclusion:

- RFC 9846 Section 1.2 lists forbidding negotiation of TLS 1.0 and TLS 1.1 as a technical change relative to RFC 8446.
- RFC 9846 Section 4.2.3 says RFC 8996 and Appendix E.5 forbid negotiating versions below TLS 1.2.
- RFC 8996 requires implementations not to negotiate TLS 1.0 or TLS 1.1 and to terminate TLS 1.0 or TLS 1.1 ClientHello attempts with `protocol_version`.
- RFC 9325 Section 3.1.1 repeats that implementations must not negotiate TLS 1.0 or TLS 1.1.

The downgrade random bytes described in RFC 9846 Section 4.2.3 are not an exception that permits negotiating TLS 1.1. They are an additional downgrade signal for noncompliant servers; Appendix E.5's "must not be negotiated for any reason" rule remains controlling.

## Relevant Source Code

### Build switch

`CMakeLists.txt:1857-1869` defines `WOLFSSL_OLD_TLS` with default value `no`. By default, CMake appends `-DNO_OLD_TLS`:

```cmake
add_option("WOLFSSL_OLD_TLS"
    "Enable old TLS versions < 1.2 (default: disabled)"
    "no" "yes;no")

if(NOT WOLFSSL_OLD_TLS)
    list(APPEND WOLFSSL_DEFINITIONS "-DNO_OLD_TLS")
else()
    if(WOLFSSL_LEAN_PSK OR WOLFSSL_LEAN_TLS)
        list(APPEND WOLFSSL_DEFINITIONS "-DNO_OLD_TLS")
        override_cache(WOLFSSL_OLD_TLS "no")
    endif()
endif()
```

The default build therefore removes old protocols in a compliant way. However, `WOLFSSL_OLD_TLS=yes` is an explicitly supported source configuration that stops defining `NO_OLD_TLS`.

### TLS 1.1 methods

`src/tls.c:348-355` constructs the TLS 1.1 version value:

```c
ProtocolVersion MakeTLSv1_1(void)
{
    ProtocolVersion pv;
    pv.major = SSLv3_MAJOR;
    pv.minor = TLSv1_1_MINOR;

    return pv;
}
```

When `NO_OLD_TLS` is not defined, the client method at `src/tls.c:19406-19421` and server method at `src/tls.c:19783-19800` are compiled and call `InitSSL_Method(method, MakeTLSv1_1())`. The example client and server map `-v 2` to the TLS 1.1-specific methods:

```c
/* examples/client/client.c:3285-3287 */
case 2:
    method = wolfTLSv1_1_client_method_ex;
    break;

/* examples/server/server.c:2724-2726 */
case 2:
    method = wolfTLSv1_1_server_method_ex;
    break;
```

`wolfssl/wolfcrypt/settings.h:5410-5413` shows hardened configurations already recognize that protocols below TLS 1.2 conflict with RFC 9325:

```c
#if !defined(NO_OLD_TLS) && !defined(WOLFSSL_HARDEN_TLS_ALLOW_OLD_TLS)
    #error "TLS < 1.2 protocol versions not allowed"
    /* https://www.rfc-editor.org/rfc/rfc9325#section-3.1.1 */
#endif
```

The ordinary old-TLS build still permits the behavior, so this is a configuration-dependent compliance issue rather than a default-build defect.

## Runtime Evidence

Fresh rerun date: 2026-08-10.

### Old-TLS build configuration

Action: wolfSSL 5.9.2 was configured with `WOLFSSL_OLD_TLS=yes`, examples enabled, a debug build type, and static library output. The client and server example targets were rebuilt.

Observed build result:

```text
[ 96%] Built target wolfssl
[100%] Built target client
[ 96%] Built target wolfssl
[100%] Built target server
```

Observed build cache state:

```text
BUILD_SHARED_LIBS:BOOL=OFF
WOLFSSL_EXAMPLES:BOOL=yes
WOLFSSL_OLD_TLS:BOOL=yes
```

The generated options header did not define `NO_OLD_TLS`, so the TLS 1.1 methods were actually compiled.

### Transparent proxy wire-level rerun

Action: a TLS 1.1 wolfSSL example server listened on a local TCP port, a transparent TCP proxy forwarded traffic to it and parsed the first ClientHello and ServerHello, and a TLS 1.1 wolfSSL example client connected through the proxy. Client, proxy, and server all exited with code `0`.

Observed client output:

```text
SSL version is TLSv1.1
SSL cipher suite is TLS_ECDHE_RSA_WITH_AES_256_CBC_SHA
SSL curve name is SECP256R1
I hear you fa shizzle!
connecting to 127.0.0.1:27114
__EXIT_CODE=0
```

Observed proxy parse:

```text
client_record_version=0302
client_hello_legacy_version=0302
server_record_version=0302
server_hello_legacy_version=0302
captured_client_to_server_bytes=384
captured_server_to_client_bytes=3216
__EXIT_CODE=0
```

Observed server output:

```text
SSL version is TLSv1.1
SSL cipher suite is TLS_ECDHE_RSA_WITH_AES_256_CBC_SHA
SSL curve name is SECP256R1
Client message: hello wolfssl!
listening on port 27113
__EXIT_CODE=0
```

Interpretation: `0x0302` is TLS 1.1. The proxy result came from the actual forwarded TCP byte stream, proving both peers negotiated TLS 1.1 and excluding explanations such as "only the API symbol exists" or "the version log is wrong".

### Default-build control

Action: the default/current build was checked as a control.

Observed build cache state:

```text
BUILD_SHARED_LIBS:BOOL=ON
WOLFSSL_EXAMPLES:BOOL=yes
WOLFSSL_OLD_TLS:BOOL=no
```

The generated options header defined `NO_OLD_TLS`:

```text
#undef NO_OLD_TLS
#define NO_OLD_TLS
```

Observed default-build client result when forced to use `-v 2`:

```text
wolfSSL error: Bad SSL version
__EXIT_CODE=1
```

Interpretation: the default build control proves the issue does not affect wolfSSL's default configuration. The old-TLS reproducer is triggered by `WOLFSSL_OLD_TLS=yes`.

## Inconsistency Reason

RFC 9846 Appendix E.5 requires TLS 1.1 not to be negotiated for any reason. wolfSSL's default build satisfies that requirement, but the source tree also provides the supported `WOLFSSL_OLD_TLS=yes` build configuration. In that configuration, a client can send a TLS 1.1 ClientHello, a server can accept it and return a TLS 1.1 ServerHello, and the peers complete handshake and application-data exchange.

If the audit scope is only wolfSSL's default build, this is not applicable. If the audit scope includes the supported build matrix or claims of conformance to RFC 9846, RFC 8996, or RFC 9325, then `WOLFSSL_OLD_TLS=yes` should be treated as a configuration-dependent compliance issue.

## Impact

- Old-TLS-enabled clients can actually send TLS 1.1 ClientHello messages.
- Old-TLS-enabled servers can accept TLS 1.1 ClientHello messages and complete TLS 1.1 handshakes.
- That configuration does not satisfy RFC 9846, RFC 8996, or RFC 9325 protocol-version requirements.
- Default users have limited exposure because the default build disables old protocols; risk is concentrated in deployments that explicitly or indirectly enable old TLS.

## Fix Direction

1. For builds claiming RFC 9846, RFC 8996, or RFC 9325 conformance, define `NO_OLD_TLS` unconditionally and prevent other configuration switches from re-enabling TLS 1.0 or TLS 1.1.
2. If old TLS is retained for legacy interoperability, label the build and documentation clearly as nonconformant with those standards.
3. Consider making TLS 1.1-specific methods fail in modern conformance builds, or isolate them into a separate legacy build target.
4. Add build-matrix tests: default and hardened builds must reject TLS 1.1; legacy build tests should be explicitly marked as nonconformant compatibility tests.
