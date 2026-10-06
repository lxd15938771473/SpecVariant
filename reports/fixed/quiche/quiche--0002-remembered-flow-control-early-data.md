# 0-RTT accepts smaller current limits than remembered limits

## Standard

RFC 9000 Section 7.4.1 requires:

```text
Remembered transport parameters apply to the new connection until the
handshake completes and the client starts sending 1-RTT packets.
```

```text
If 0-RTT data is accepted by the server, the server MUST NOT reduce
any limits ... with its 0-RTT data.
```

```text
A server MUST reject 0-RTT data if the restored values for transport
parameters cannot be supported.
```

The listed limits include:

```text
initial_max_data
initial_max_stream_data_bidi_local
initial_max_stream_data_bidi_remote
initial_max_stream_data_uni
initial_max_streams_bidi
initial_max_streams_uni
```

RFC 9001 Sections 4.6.2 and 4.6.3 add:

```text
A server rejects 0-RTT ... MUST NOT process any 0-RTT packets.
```

```text
This associated state is used for deciding whether 0-RTT data must be
rejected.
```

RFC 9000 Section 17.2.3 also says:

```text
A server SHOULD treat a violation of remembered limits ... as a
connection error of an appropriate type
```

That text applies only when the client violates the remembered limit itself.
It does not allow the server to accept 0-RTT and then apply a newer, smaller
limit.

## Code

Client-side remembered limits are implemented:

- `quiche/src/tls/mod.rs:993-1040`: `new_session()` serializes `peer_params`
  together with the TLS session.
- `quiche/src/lib.rs:2400-2418`: `set_session()` decodes the saved transport
  parameters.
- `quiche/src/lib.rs:7959-7970`: `process_peer_transport_params()` restores
  `initial_max_data` and `initial_max_streams_*`.

Server-side 0-RTT acceptance is not tied to remembered transport state:

- `quiche/src/tls/mod.rs:392-394`

```rust
// TODO: the early data context should include transport parameters and
// HTTP/3 SETTINGS in wire format.
self.set_quic_early_data_context(b"quiche")?;
```

I did not find a quiche path that compares remembered server transport limits
with the current server configuration before accepting 0-RTT.

Incoming 0-RTT is then checked against the current local limits:

- `quiche/src/stream/mod.rs:259-277`: new remote streams use current
  `local_params` stream flow-control values.
- `quiche/src/stream/mod.rs:313-333`: remote stream count is checked against
  current `self.local_max_streams_*`, returning `Error::StreamLimit`.
- `quiche/src/lib.rs:8569-8575`: connection-level receive flow control is
  checked against the current limit, returning `Error::FlowControl`.

So the client sends 0-RTT using remembered limits, the server enters early
data, and the server then enforces smaller current limits while processing
those 0-RTT packets.

## Runtime Evidence

On 2026-08-12 I ran a temporary focused reproducer and removed it after
verification.

Command:

```text
cargo test -p quiche handshake_0rtt_runtime_repro_smaller_current_initial_max_data --lib -- --nocapture
```

Observed output:

```text
running 2 tests
test tests::handshake_0rtt_runtime_repro_smaller_current_initial_max_data::cc_algorithm_name_1___cubic__ ... ok
test tests::handshake_0rtt_runtime_repro_smaller_current_initial_max_data::cc_algorithm_name_2___bbr2_gcongestion__ ... ok

test result: ok. 2 passed; 0 failed; 0 ignored; 0 measured; 1089 filtered out
```

Reproducer summary:

1. Initial connection advertises `initial_max_data = 30`.
2. Client saves the session.
3. Resumed connection changes only the current server config to
   `initial_max_data = 5`.
4. Both endpoints enter early data.
5. Client sends 12 bytes of 0-RTT stream data.
6. Server returns `Err(Error::FlowControl)`.

The 12-byte send is legal under the remembered limit `30`, but illegal under
the current smaller limit `5`. Therefore the server did not reject 0-RTT up
front; it accepted 0-RTT and then enforced the smaller current limit.

Run log: `0002-remembered-flow-control-early-data.runtime-20260812.log`

After removing the temporary reproducer, I reran:

```text
cargo test -p quiche handshake_0rtt --lib -- --nocapture
```

Result: `7 passed`.

## Conclusion

This is a real issue.

The client does remember and restore the previous server transport limits. The
bug is on the server side: when current transport limits are smaller than the
remembered ones, quiche still accepts 0-RTT and then applies the smaller
current limits while processing 0-RTT packets.

That behavior conflicts with RFC 9000 Section 7.4.1 and RFC 9001 Sections
4.6.2-4.6.3, so the report should be kept.
