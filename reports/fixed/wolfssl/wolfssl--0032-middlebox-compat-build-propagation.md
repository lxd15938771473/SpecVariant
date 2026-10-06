# CMake does not propagate the Middlebox Compatibility macro to libwolfssl, producing an empty ClientHello legacy_session_id

## Summary

- Verdict: `issue_found`
- Confidence: `high`
- Root Cause Key: `middlebox-compat-build-propagation`
- Covered Record ID: `cand-411efe37c923-boundary`

CMake can generate a public `wolfssl/options.h` that defines `WOLFSSL_TLS13_MIDDLEBOX_COMPAT`, but it does not pass the macro when compiling `libwolfssl`. The resulting package advertises TLS 1.3 Middlebox Compatibility to downstream code while the library itself is compiled in non-compatibility mode. Its initial ClientHello therefore carries a zero-length `legacy_session_id`.

## Standard Requirement

- Standard: [RFC 8446](https://www.rfc-editor.org/rfc/rfc8446.html)
- Section: `4.1.2 Client Hello`

```text
In compatibility mode (see Appendix D.4),
this field MUST be non-empty, so a client not offering a
pre-TLS 1.3 session MUST generate a new 32-byte value.
```

This requirement applies only in compatibility mode. The issue is that the build output is configured and publicly marked as having that mode enabled, while the compiled library behaves as though it were disabled.

## Build-System Evidence

### CMake accepts the variable without defining an independent option

The CMake cache contained:

```text
WOLFSSL_TLS13_MIDDLEBOX_COMPAT:UNINITIALIZED=yes
```

At `CMakeLists.txt:390`, the macro is added to `WOLFSSL_DEFINITIONS` only inside the `WOLFSSL_JNI` branch. No independent option is declared for `WOLFSSL_TLS13_MIDDLEBOX_COMPAT`. The command-line variable therefore enters the cache without being consistently propagated to the library target.

### Public header and library compilation configuration disagree

The generated `wolfssl/options.h:782` contains:

```c
#undef WOLFSSL_TLS13_MIDDLEBOX_COMPAT
#define WOLFSSL_TLS13_MIDDLEBOX_COMPAT
```

However, `CMakeFiles/wolfssl.dir/flags.make:5` does not contain `-DWOLFSSL_TLS13_MIDDLEBOX_COMPAT`, and the `config.h` used for the build does not define the macro.

At `wolfssl/wolfcrypt/libwolfssl_sources.h:36`, library sources include `config.h`; they do not rely on the generated `wolfssl/options.h` supplied to downstream consumers. Preprocessor checks consequently produced different answers:

- Including `wolfssl/options.h` reported the macro as defined.
- Preprocessing under the actual `libwolfssl` source-build environment reported the macro as undefined.

## Relevant Source Code

### `src/tls13.c:4544-4580`

`GetTls13SessionId()` writes the 32-byte value only when the macro is compiled in and compatibility mode is enabled at runtime. Otherwise, it writes a zero length:

```c
#ifdef WOLFSSL_TLS13_MIDDLEBOX_COMPAT
    if (ssl->options.tls13MiddleBoxCompat) {
        output[*idx] = ID_LEN;
        XMEMCPY(output + *idx + 1, ssl->arrays->clientRandom, ID_LEN);
        *idx += ID_LEN + 1;
    }
    else
#endif
    {
        output[*idx] = 0;
        (*idx)++;
    }
```

Because the macro is invisible when `libwolfssl` is compiled, the preprocessor removes the entire compatibility branch. A later definition in the public header cannot change the behavior of the already compiled library.

## Implementation Behavior

1. The CMake cache accepts the Middlebox Compatibility variable.
2. The generated public header says that the feature is enabled.
3. The macro is absent from the `libwolfssl` compilation definitions and build-time `config.h`.
4. The compatibility branch in `GetTls13SessionId()` is not compiled into the library.
5. The emitted ClientHello uses an empty `legacy_session_id`.

## Runtime Evidence

### Generated `mbox` example client

The test ran the generated `mbox` example client against a local listener that captured the first TLS record. The listener captured a 513-byte ClientHello and inspected the byte immediately following the ClientHello random. The observed `legacy_session_id` length was `0`, proving that the wire message contained an empty vector.

### Minimal linked-library probe

A separate probe linked directly against the audited `libwolfssl.a`, queried the initial compatibility state, initiated a connection through a capture callback, and parsed the emitted ClientHello. It observed:

```text
initial_middlebox=0
initial_session_id_sz=0
connect_ret=-1
connect_err=2
captured_sends=1
captured_len=1426
captured_sid_len=0
```

The independent probe therefore confirmed both that compatibility mode was disabled inside the compiled library and that the emitted ClientHello encoded `legacy_session_id` with length `0`. This directly connects the build-propagation defect to the protocol-visible behavior.

## Inconsistency Reason

The build system makes the public header state that `WOLFSSL_TLS13_MIDDLEBOX_COMPAT` is enabled without compiling `libwolfssl` with the same configuration. Downstream capability reporting, internal library state, and wire behavior therefore disagree. For the `mbox` artifact intended to enable compatibility mode, an empty `legacy_session_id` is inconsistent with RFC 8446 Section 4.1.2.

## Impact

- Applications that inspect the public header receive incorrect build-capability information.
- Clients expected to operate in compatibility mode do not generate the required 32-byte `legacy_session_id`.
- Deployments that rely on this mode to accommodate legacy middleboxes may still fail to connect.

## Fix Direction

- Declare an independent Middlebox Compatibility CMake option.
- Propagate the option consistently to the `libwolfssl` target definitions, the build-time `config.h`, and the public `wolfssl/options.h`.
- Add a build test that asserts consistent macro state across the cache, configuration headers, and library target compiler flags.
- Add a wire-level regression test confirming that a non-resumption TLS 1.3 ClientHello carries a 32-byte `legacy_session_id` when the option is enabled.
