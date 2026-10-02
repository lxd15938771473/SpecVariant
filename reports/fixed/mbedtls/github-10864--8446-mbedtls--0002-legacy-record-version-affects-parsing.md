# TLS 1.3 Server Still Lets `legacy_record_version` Affect Inbound Record Parsing

## Summary

RFC 8446 distinguishes the record-layer `TLSPlaintext.legacy_record_version`, which TLS 1.3 receivers must ignore, from the handshake-layer `ClientHello.legacy_version` and `supported_versions`, which participate in version negotiation.

mbedTLS largely follows this distinction in its TLS 1.3 handshake logic, but its earlier record-header parser still reads the wire `legacy_record_version` and rejects a value above `max_tls_version` with `MBEDTLS_ERR_SSL_INVALID_RECORD`. The supposedly ignored field therefore changes whether the inbound record is accepted before ClientHello parsing begins.

This report incorporates the former conclusion for `req-2b3ec3a9ddb4712c6f31`, which described the same behavior from the version-negotiation perspective. The more direct root cause is the cleartext record-header rejection before handshake parsing.

## Standard Requirement

- [RFC 8446 Section 5.1, Record Layer](https://www.rfc-editor.org/rfc/rfc8446.html#section-5.1)
- [RFC 8446 Section 4.2.1, Supported Versions](https://www.rfc-editor.org/rfc/rfc8446.html#section-4.2.1)
- [RFC 8446 Appendix D, Backward Compatibility](https://www.rfc-editor.org/rfc/rfc8446.html#appendix-D)

RFC 8446 deprecates `TLSPlaintext.legacy_record_version` in TLS 1.3 and says that receivers must ignore it for all purposes. Appendix D repeats that all implementations must ignore the field and specifically notes that servers will receive varied TLS 1.x record versions whose values must always be ignored. The implementer checklist likewise calls for ignoring the record-layer version number in all unencrypted TLS records.

Version negotiation belongs to the handshake layer. When `supported_versions` is present, the server must negotiate using that extension rather than `ClientHello.legacy_version`. These rules separate the fields that must be interpreted from the record-header field that must not affect processing.

## Relevant Source Code

### `library/ssl_msg.c:4721`

`ssl_get_next_record()` fetches the inbound header and calls `ssl_parse_record_header()` before any ClientHello parsing. An error here prevents the handshake layer from examining the message.

### `library/ssl_msg.c:3667`

The record parser reads `rec->ver`, converts it with `mbedtls_ssl_read_version()`, and returns `MBEDTLS_ERR_SSL_INVALID_RECORD` when the resulting value is greater than `ssl->conf->max_tls_version`.

### `library/ssl_msg.c:6215`

For TLS transport, `mbedtls_ssl_read_version()` reads the 16-bit record value without a TLS 1.3 exception that would ignore a cleartext record version.

### `library/ssl_tls13_server.c:1288`

The handshake parser separately validates `ClientHello.legacy_version == 0x0303`, which is a legitimate TLS 1.3 handshake-field requirement and is not the defect.

### `library/ssl_tls13_server.c:1373`

The server parses `supported_versions` and selects TLS 1.2 or TLS 1.3 from the handshake extension. This confirms that the handshake layer knows the correct negotiation source; the contradiction lies in the earlier record-header gate.

## Implementation Behavior

The record path parses the cleartext record version before the handshake content. A value above the configured maximum stops processing as an invalid record. A lower unusual value can pass the same gate and still lead to successful TLS 1.3 negotiation through `supported_versions`. Thus, the wire header value does influence acceptance and is not ignored for all purposes.

## Inconsistency Reason

RFC 8446 requires the TLS 1.3 receiver to ignore `TLSPlaintext.legacy_record_version`. mbedTLS uses the field as a record-acceptance predicate in `ssl_parse_record_header()`. A field required to be semantically inert can therefore decide whether the handshake is parsed.

## Runtime Evidence

A four-part proxy probe was rerun on `2026-08-04`:

1. A normal TLS 1.3 handshake completed with client exit code `0`, protocol `TLSv1.3`, and an HTTP `200 OK` response. This validated the server, client, certificate, ports, and proxy path.
2. Capturing the unmodified first ClientHello record produced header `16030300d5`, confirming the normal `legacy_record_version` value `0303`.
3. The proxy changed only those two header bytes from `0303` to `7a7a`, producing `167a7a00d5`; all handshake bytes remained unchanged. Both peers exited with code `1`, the client reported a reset, and the server reported `An invalid SSL record was received`.
4. Through the same proxy path, changing only the field to the lower value `0200` produced `16020000d5`. The handshake still completed as TLS 1.3 with client exit code `0`, HTTP `200 OK`, and server cipher `TLS1-3-CHACHA20-POLY1305-SHA256`.

All four probe modes reported successful test execution. The contrast shows that header rewriting itself did not generically corrupt the handshake: the high value specifically triggered the `tls_version > max_tls_version` gate, while the low value did not.

## Impact

The server rejects otherwise parseable TLS 1.3 ClientHello records based solely on a record-layer field that the RFC requires it to ignore. This causes avoidable interoperability and conformance failures before the actual version-negotiation fields are processed.

## Fix Direction

Do not apply the record-version upper-bound rejection to TLS 1.3 cleartext records. Preserve the legitimate handshake-layer checks for `ClientHello.legacy_version` and `supported_versions`. Add regression cases that vary only the cleartext record version above and below `0x0303` while keeping a valid TLS 1.3 ClientHello.

## Consolidation Record

This report also incorporates former requirement `req-72038e2bd8e239a553b1`, which covers the same inbound `legacy_record_version` root cause.
