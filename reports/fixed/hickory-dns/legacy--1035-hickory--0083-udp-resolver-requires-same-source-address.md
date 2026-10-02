# UDP resolver requires same source address

## Summary

Implementation detail: UDP response matching requires the packet source IP and port to equal the query destination.

This is a real [RFC 1035](https://www.rfc-editor.org/rfc/rfc1035.html) compatibility issue. The behavior may be an intentional modern anti-spoofing choice, but it directly conflicts with the [RFC 1035](https://www.rfc-editor.org/rfc/rfc1035.html) rule checked here.

## Standard Requirement

Official standard: [RFC 1035 Section 7.3](https://www.rfc-editor.org/rfc/rfc1035.html#section-7.3), "Processing responses".

Key text:

```text
resolver cannot rely that a response will come from the same address
```

Standard reference:

- [RFC 1035 Section 7.3](https://www.rfc-editor.org/rfc/rfc1035.html#section-7.3): match responses to current resolver requests using DNS ID, then verify the Question section.
- [RFC 1035 Section 7.3](https://www.rfc-editor.org/rfc/rfc1035.html#section-7.3): some name servers respond from a different address than the one that received the query.

Requirement interpretation: a resolver must not make same source address a mandatory condition for matching a response. The old report's local line range `2595-2599` was stale; the relevant text is at `2550-2554`.

## Relevant Source Code

`crates/net/src/udp/udp_client_stream.rs:219-251` rejects a UDP response before parsing the DNS ID if the source endpoint differs:

```rust
let (len, src) = socket.recv_from(&mut recv_buf).await?;
let request_target = msg.addr();

if src.ip().to_canonical() != request_target.ip().to_canonical()
    || src.port() != request_target.port()
{
    warn!("ignoring response from ... because it does not match name_server ...");
    continue;
}
```

`crates/net/src/udp/udp_client_stream.rs:253-270` checks the DNS ID only after that endpoint check:

```rust
let Some(id_bytes) = response_buffer.first_chunk::<2>() else {
    continue;
};
let response_id = u16::from_be_bytes(*id_bytes);

if msg_id != response_id {
    continue;
}
```

`crates/net/src/udp/udp_client_stream.rs:286-333` also validates the Question section after parsing, but a different-source response cannot reach that path because it is discarded earlier.

## Implementation Behavior

Hickory's UDP client sends a query to `request_target`, then requires every response to come from the same canonical IP and same port. A response with the correct DNS ID and matching Question is ignored if it comes from a different source address.

## Inconsistency Reason

[RFC 1035](https://www.rfc-editor.org/rfc/rfc1035.html) says resolvers cannot rely on same-address responses and recommends matching by DNS ID plus Question section. Hickory makes same source IP/port a hard precondition before those checks, so it rejects the legacy/buggy server behavior [RFC 1035](https://www.rfc-editor.org/rfc/rfc1035.html) explicitly describes.

## Runtime Evidence

The recorded focused test sent a UDP query to `127.0.0.1:port`, then sent a syntactically valid response with the same DNS ID and Question from `127.0.0.2` using the same port. It ran with `cargo test -q -p hickory-net udp_client_stream_ignores_same_id_response_from_different_source_ip -- --nocapture`. The captured run used port 54339: the alternate-source response was sent, but the client returned `Err(Timeout)`. The observation test passed. In conjunction with the source-address check shown above, this demonstrates rejection of the matching response when its source IP differs from the query destination.

Recorded inputs, output, and checks:

```text
sent matching response from alternate source 127.0.0.2:54339 instead of 127.0.0.1:54339
client result after alternate-source response: Err(Timeout)
test result: ok. 1 passed; 0 failed
```

## Impact

Resolvers using this UDP path can fail against [RFC 1035](https://www.rfc-editor.org/rfc/rfc1035.html)-described name servers that reply from a different source address. Strict matching improves spoofing resistance, so this is best treated as a compatibility/security tradeoff, not a crash or parsing bug.

## Fix Direction

Keep strict endpoint matching as the secure default, but add an explicit compatibility mode that can accept responses matched by DNS ID and Question section when an operator needs [RFC 1035](https://www.rfc-editor.org/rfc/rfc1035.html) legacy behavior.
