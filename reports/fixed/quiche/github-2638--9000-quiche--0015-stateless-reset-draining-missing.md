# Stateless reset skips draining

## Standard Requirement

- RFC 9000 Section 10.3: [Immediate Close](https://www.rfc-editor.org/rfc/rfc9000.html#section-10.3)
- RFC 9000 Section 10.3.1: [Detecting a Stateless Reset](https://www.rfc-editor.org/rfc/rfc9000.html#section-10.3.1)
- RFC 9000 Section 10.2.2: [Draining Connection State](https://www.rfc-editor.org/rfc/rfc9000.html#section-10.2.2)

RFC 9000 says a peer that receives a Stateless Reset will immediately end the connection. The concrete detection rule is:

```text
If the last 16 bytes of the datagram are identical in value to a
stateless reset token, the endpoint MUST enter the draining period
and not send any further packets on this connection.
```

RFC 9000 makes draining distinct from the closed state: the endpoint stops sending, keeps connection state until the draining period ends, and only then discards the state.

## Relevant Source Code

`quiche/src/lib.rs:2870-2877` handles stateless reset detection by closing immediately:

```rust
if self.is_stateless_reset(&buf[len - left..len]) {
    trace!("{} packet is a stateless reset", self.trace_id);

    self.mark_closed();
}
```

- `quiche/src/lib.rs:7782-7783`: `is_draining()` is `self.draining_timer.is_some()`.
- `quiche/src/lib.rs:7023-7079`: draining keeps a timer and closes only when that timer expires.
- `quiche/src/lib.rs:8781-8802`: receiving `CONNECTION_CLOSE` sets `draining_timer`.
- `quiche/src/lib.rs:9217-9309`: `mark_closed()` sets `closed = true` and does not arm draining.

## Runtime Evidence

- Date: `2026-08-12`
- Command: `$env:CARGO_TARGET_DIR='..\\..\\implementions\\quiche\\target'; cargo run --quiet`

```text
scenario=connection_close
after_recv is_closed=false is_draining=true timeout_is_some=true
scenario=stateless_reset
after_recv is_closed=true is_draining=false timeout_is_some=false
send_after_reset=Err(Done)
```

The same build enters draining for `CONNECTION_CLOSE`, but skips draining and becomes fully closed immediately for stateless reset.

## Conclusion

quiche treats stateless reset as `closed=true` instead of entering draining. That contradicts RFC 9000 10.3.1, so this is a real issue.
