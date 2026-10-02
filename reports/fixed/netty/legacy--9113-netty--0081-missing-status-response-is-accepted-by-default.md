# Missing `:status` response is accepted by default

## Problem Description

An HTTP/2 response without `:status` is malformed. Netty can reject it when `validateRequiredPseudoHeaders` is enabled, but the default client decoder leaves that flag off and delivers the malformed response upstream.

## Standard Requirement

Official standard: [RFC 9113 Section 8.3.2](https://www.rfc-editor.org/rfc/rfc9113.html#section-8.3.2)

[RFC 9113 Section 8.3.2](https://www.rfc-editor.org/rfc/rfc9113.html#section-8.3.2), [RFC 9113 Section 8.3.2](https://www.rfc-editor.org/rfc/rfc9113.html#section-8.3.2):

```text
   For HTTP/2 responses, a single ":status" pseudo-header field is
   defined that carries the HTTP status code field (see Section 15 of
   [HTTP]).  This pseudo-header field MUST be included in all responses,
   including interim responses; otherwise, the response is malformed
   (Section 8.1.1).
```

[RFC 9113 Section 8.1.1](https://www.rfc-editor.org/rfc/rfc9113.html#section-8.1.1), [RFC 9113 Section 8.1.1](https://www.rfc-editor.org/rfc/rfc9113.html#section-8.1.1):

```text
   A malformed request or response is one that is an otherwise valid
   sequence of HTTP/2 frames but is invalid due to the presence of
   extraneous frames, prohibited fields or pseudo-header fields, the
   absence of mandatory pseudo-header fields, the inclusion of uppercase
   field names, or invalid field names and/or values (in certain
   circumstances; see Section 8.2).

   Malformed requests or responses that are detected MUST be treated as a
   stream error (Section 5.4.2) of type PROTOCOL_ERROR.

   Clients MUST NOT accept a malformed response.
```

So a client receiving response HEADERS without `:status` must treat the response as malformed and must not accept it.

## Relevant Source Code

`implementions/netty-4.2/codec-http2/src/main/java/io/netty/handler/codec/http2/DefaultHttp2ConnectionDecoder.java:287-321`

```java
private static void validateRequiredPseudoHeaders(boolean server, int streamId, Http2Headers headers)
        throws Http2Exception {
    if (server) {
        // request checks omitted
    } else {
        if (headers.status() == null) {
            throw streamError(streamId, PROTOCOL_ERROR,
                    "Response is missing mandatory :status pseudo-header field.");
        }
    }
}
```

`DefaultHttp2ConnectionDecoder.java:467-471`

```java
if (!isTrailers) {
    if (validateRequiredPseudoHeaders && !isInformational) {
        validateRequiredPseudoHeaders(connection.isServer(), stream.id(), headers);
    }
```

`DefaultHttp2ConnectionDecoder.java:128-135`

```java
public DefaultHttp2ConnectionDecoder(..., boolean validateHeaders) {
    this(connection, encoder, frameReader, requestVerifier, autoAckSettings, autoAckPing, validateHeaders, false);
}
```

`AbstractHttp2ConnectionHandlerBuilder.java:300-315`

```java
protected boolean isValidateRequiredPseudoHeaders() {
    return validateRequiredPseudoHeaders != null ? validateRequiredPseudoHeaders : false;
}

protected B validateRequiredPseudoHeaders(boolean validateRequiredPseudoHeaders) {
    this.validateRequiredPseudoHeaders = validateRequiredPseudoHeaders;
```

`Http2FrameCodecBuilder.java:246-248` and `Http2MultiplexCodecBuilder.java:261-263` pass `isValidateRequiredPseudoHeaders()` into the decoder, so normal builder-created codecs inherit the default `false` unless the application opts in.

## Runtime Evidence

The temporary client-decoder tests supplied response HEADERS without `:status` under default and strict mandatory-pseudo-header validation. The default client called the listener; the strict client threw `PROTOCOL_ERROR` with a missing-`:status` diagnostic. Both observation-based tests passed.

Probe: `SpecLitmusStatusPseudoHeaderProbeTest-snippet`.

Rerun command:

```text
.\mvnw.cmd -pl codec-http2 -Dtest=DefaultHttp2ConnectionDecoderTest#specLitmusDefaultClientAcceptsResponseWithoutStatus+specLitmusStrictClientRejectsResponseWithoutStatus -Dsurefire.failIfNoSpecifiedTests=false -DskipITs=true -DskipHttp2Tests=false -DskipNativeTests=true -Dcheckstyle.skip=true -DskipJapicmp=true test
```

Observed output:

```text
case=default-client-missing-status expected=rejected actual=accepted listener=called
case=strict-client-missing-status expected=rejected actual=rejected error=PROTOCOL_ERROR message=Response is missing mandatory :status pseudo-header field.
Tests run: 2, Failures: 0, Errors: 0, Skipped: 0
BUILD SUCCESS
Finished at: 2026-09-10T08:17:16+08:00
```

The default path accepted and forwarded the malformed response. The strict path rejected the same input, which proves the implementation has the check but does not enable it by default.

## Inconsistency Reason

[RFC 9113](https://www.rfc-editor.org/rfc/rfc9113.html) requires clients to reject malformed responses missing `:status`. Netty's default client decoder skips mandatory pseudo-header validation because `validateRequiredPseudoHeaders` defaults to `false`, so malformed response HEADERS reach the application listener.

## Remaining Uncertainty

None for the tested default client response path.
