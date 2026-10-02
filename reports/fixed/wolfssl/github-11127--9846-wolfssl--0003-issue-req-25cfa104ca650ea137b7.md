# RFC9846 wolfSSL issue: NewSessionTicket extension TLV framing is skipped without early-data support

## Summary
- Not applicable: builds without session-ticket/resumption support, because RFC9846 permits clients that do not support resumption to silently ignore the whole `NewSessionTicket` message.

wolfSSL parses and stores TLS 1.3 `NewSessionTicket` messages when session tickets are compiled in. In the configuration where session tickets are enabled but `WOLFSSL_EARLY_DATA` is disabled, `DoTls13NewSessionTicket` validates only the outer `extensions` vector length and skips the vector contents. Therefore malformed `Extension` TLV framing inside `NewSessionTicket.extensions` is accepted instead of causing a `decode_error`.

## Standard Requirement

Official standard links:

- [RFC 9846 Section 4.3, Extensions](https://datatracker.ietf.org/doc/html/rfc9846#section-4.3)
- [RFC 9846 Section 4.7.1, New Session Ticket Message](https://datatracker.ietf.org/doc/html/rfc9846#section-4.7.1)
- [RFC 9846 Section 6, Alert Protocol](https://datatracker.ietf.org/doc/html/rfc9846#section-6)

```text
A number of TLS messages contain tag-length-value encoded extensions
structures.

struct {
    ExtensionType extension_type;
    opaque extension_data<0..2^16-1>;
} Extension;
```

```text
There MUST NOT be more than one extension of the same type in a given
extension block.
```

```text
Clients which receive a NewSessionTicket message but do not support
resumption MUST silently ignore this message.
```

```text
struct {
    uint32 ticket_lifetime;
    uint32 ticket_age_add;
    opaque ticket_nonce<0..255>;
    opaque ticket<1..2^16-1>;
    Extension extensions<0..2^16-1>;
} NewSessionTicket;
```

```text
extensions: A list of extension values for the ticket. The "Extension"
format is defined in Section 4.3. Clients MUST ignore unrecognized
extensions.
```

```text
Peers which receive a message which cannot be parsed according to the
syntax (e.g., have a length extending beyond the message boundary or
contain an out-of-range length) MUST terminate the connection with a
"decode_error" alert.
```

Interpretation: a client that does not support resumption may ignore the entire `NewSessionTicket`. A client that does support resumption and processes the ticket must still parse `extensions` as a list of Section 4.3 `Extension` TLVs. The instruction to ignore unrecognized extensions applies to well-formed unknown extension types; it does not permit accepting bytes that cannot be parsed as `Extension` structures at all.

## Relevant Source Code

### `src/tls13.c:12880-12892`

```c
    if ((*inOutIdx - begin) + EXTS_SZ > size)
        return BUFFER_ERROR;
    ato16(input + *inOutIdx, &length);
    *inOutIdx += EXTS_SZ;
    if ((*inOutIdx - begin) + length != size)
        return BUFFER_ERROR;
    #ifdef WOLFSSL_EARLY_DATA
    ret = TLSX_Parse(ssl, (byte *)input + (*inOutIdx), length, session_ticket,
                     NULL);
    if (ret != 0)
        return ret;
    #endif
    *inOutIdx += length;
```

This path checks that the two-byte `extensions` vector length reaches the end of the `NewSessionTicket` body. It calls the generic extension parser only when `WOLFSSL_EARLY_DATA` is compiled in.

### `src/tls13.c:12900-12907`

```c
#else
    (void)ssl;
    (void)input;

    WOLFSSL_ENTER("DoTls13NewSessionTicket");

    *inOutIdx += size;
#endif /* HAVE_SESSION_TICKET */
```

When `HAVE_SESSION_TICKET` is not compiled in, wolfSSL ignores the whole message. That path matches the RFC9846 exception for clients that do not support resumption.

### `src/tls.c:18421-18476`

```c
        if (length - offset < HELLO_EXT_TYPE_SZ + OPAQUE16_LEN)
            return BUFFER_ERROR;

        ato16(input + offset, &type);
        offset += HELLO_EXT_TYPE_SZ;

        ato16(input + offset, &size);
        offset += OPAQUE16_LEN;

        if (length - offset < size)
            return BUFFER_ERROR;
```

`TLSX_Parse` performs the generic `Extension` TLV framing checks. It rejects a short extension header and rejects an extension whose declared `extension_data` length runs past the extension block.

### `src/tls.c:18444-18450` and `src/internal.c:37153-37167`

```c
            if (IS_OFF(seenType, TLSX_ToSemaphore(type))) {
                TURN_ON(seenType, TLSX_ToSemaphore(type));
            }
            else {
                return DUPLICATE_TLS_EXT_E;
            }
```

```c
    int TranslateErrorToAlert(int err)
    {
        switch (err) {
            case WC_NO_ERR_TRACE(BUFFER_ERROR):
                return decode_error;
            ...
            case WC_NO_ERR_TRACE(DUPLICATE_TLS_EXT_E):
                return illegal_parameter;
```

The same generic parser also catches duplicate recognized extensions. Syntax framing failures map to `decode_error`; duplicate recognized extensions map to `illegal_parameter`.

## Implementation Behavior

In `HAVE_SESSION_TICKET && !WOLFSSL_EARLY_DATA` builds, `DoTls13NewSessionTicket` parses the fixed ticket fields, stores ticket state, validates only the outer `extensions` vector length, skips all extension bytes, calls `SetupSession`, and returns success. No internal `ExtensionType` or `extension_data` length scan is performed.

In `HAVE_SESSION_TICKET && WOLFSSL_EARLY_DATA` builds, the same function calls `TLSX_Parse` for `session_ticket`. That parser rejects malformed TLV framing with `BUFFER_ERROR` and duplicate recognized extensions with `DUPLICATE_TLS_EXT_E`.

In builds without `HAVE_SESSION_TICKET`, the whole `NewSessionTicket` message is ignored. That is not an issue under the standard's no-resumption exception.

## Inconsistency Reason

RFC9846 separates three cases:

- A client that does not support resumption may silently ignore the entire `NewSessionTicket`.
- A client that supports resumption may ignore a well-formed but unrecognized extension.
- A peer that receives a message whose fields cannot be parsed according to the TLS syntax must terminate with `decode_error`.

wolfSSL's non-early-data ticket build falls into the second implementation category but behaves as if malformed extension bytes were merely unknown extensions. Because it stores and processes the ticket while skipping the `Extension` TLV framing, malformed `NewSessionTicket.extensions` payloads with a matching outer vector length are accepted.

## Runtime Evidence

Runtime was rerun on 2026-08-10 from the workspace root.

Action: the non-early-data and early-data loopback targets were rebuilt. Both rebuilds completed successfully. The loopback test established a real TLS 1.3 client/server connection, had the server send an encrypted post-handshake `NewSessionTicket` built through wolfSSL's own record builder, and then sent one application-data marker byte. Reading the marker means the client accepted or skipped the crafted ticket; rejection before the marker means the ticket parser failed the crafted input.

Observed result for a valid unknown extension in the ticket-enabled non-early-data build:

```text
mode=valid_unknown_extension
compile_have_session_ticket=defined
compile_wolfssl_early_data=not_defined
client_note=client read application marker after crafted NST
observed=accepted_post_handshake_message
issue_observed=not_applicable_control
```

Observed result for malformed ticket extensions in the ticket-enabled non-early-data build:

```text
mode=malformed_short_vector
compile_have_session_ticket=defined
compile_wolfssl_early_data=not_defined
client_note=client read application marker after crafted NST
client_alert_tx_code=-1
observed=accepted_post_handshake_message
issue_observed=yes

mode=malformed_inner_length_truncation
compile_have_session_ticket=defined
compile_wolfssl_early_data=not_defined
client_note=client read application marker after crafted NST
client_alert_tx_code=-1
observed=accepted_post_handshake_message
issue_observed=yes
```

Observed result for the same malformed ticket extensions with early-data enabled:

```text
mode=malformed_short_vector
compile_have_session_ticket=defined
compile_wolfssl_early_data=defined
client_note=client rejected before marker, read_ret=-1 ssl_error=-328
client_alert_tx_code=50
client_alert_tx_level=2
observed=rejected_post_handshake_message
issue_observed=no

mode=malformed_inner_length_truncation
compile_have_session_ticket=defined
compile_wolfssl_early_data=defined
client_note=client rejected before marker, read_ret=-1 ssl_error=-328
client_alert_tx_code=50
client_alert_tx_level=2
observed=rejected_post_handshake_message
issue_observed=no
```

Interpretation: the loopback rerun confirms that the non-early-data build accepts malformed `NewSessionTicket.extensions` and continues to read application data, while the early-data build rejects the same malformed records with alert code `50` (`decode_error`).

Action: a focused dispatcher probe directly called wolfSSL's TLS 1.3 `DoTls13HandShakeMsgType(..., session_ticket, ...)` dispatcher after placing the client object in a legitimate post-handshake receive state. This narrower parser-path check verifies the exact `DoTls13NewSessionTicket` branch.

Observed result in the ticket-enabled non-early-data build:

```text
build_WOLFSSL_EARLY_DATA=0
case=empty_vector accepted=1 ret=0 consumed=14 alert_level=-1 alert_code=-1
case=well_formed_unknown accepted=1 ret=0 consumed=18 alert_level=-1 alert_code=-1
case=well_formed_early_data accepted=1 ret=0 consumed=22 alert_level=-1 alert_code=-1
case=short_extension_header accepted=1 ret=0 consumed=15 alert_level=-1 alert_code=-1
case=truncated_extension_value accepted=1 ret=0 consumed=18 alert_level=-1 alert_code=-1
case=duplicate_early_data accepted=1 ret=0 consumed=30 alert_level=-1 alert_code=-1
probe_result=passed
```

Observed result in the ticket-enabled early-data build:

```text
build_WOLFSSL_EARLY_DATA=1
case=empty_vector accepted=1 ret=0 consumed=14 alert_level=-1 alert_code=-1
case=well_formed_unknown accepted=1 ret=0 consumed=18 alert_level=-1 alert_code=-1
case=well_formed_early_data accepted=1 ret=0 consumed=22 alert_level=-1 alert_code=-1
case=short_extension_header accepted=0 ret=-328 consumed=14 alert_level=2 alert_code=50
case=truncated_extension_value accepted=0 ret=-328 consumed=14 alert_level=2 alert_code=50
case=duplicate_early_data accepted=0 ret=-457 consumed=14 alert_level=2 alert_code=47
probe_result=passed
```

Interpretation: the dispatcher probe confirms the same configuration split. The non-early-data build accepts malformed and duplicate `NewSessionTicket` extensions. The early-data build rejects malformed TLV framing with `decode_error` and duplicate `early_data` with `illegal_parameter`.

## Impact

A nonconformant or malicious authenticated TLS 1.3 server can send a malformed encrypted post-handshake `NewSessionTicket.extensions` vector to a wolfSSL client built with session tickets but without early data. The client accepts the message and continues the connection instead of terminating with the required `decode_error`. The main impact is standards compliance and interoperability; the sender must already be the TLS peer capable of producing valid encrypted post-handshake traffic.

## Fix Direction

Validate generic `Extension` TLV framing for `NewSessionTicket.extensions` whenever `HAVE_SESSION_TICKET` is enabled, regardless of `WOLFSSL_EARLY_DATA`. A direct fix is to call `TLSX_Parse` for `session_ticket` unconditionally and keep extension-specific handling conditional where necessary. An alternative is a small structural scanner that enforces `ExtensionType` plus `extension_data<0..2^16-1>` boundaries and duplicate checks while still ignoring well-formed unrecognized extension types.
