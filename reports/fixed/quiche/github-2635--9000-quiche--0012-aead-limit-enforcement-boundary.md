# AEAD usage-limit enforcement is missing

## Summary

`quiche` defines `AEAD_LIMIT_REACHED (0x0f)`, but it does not implement the RFC 9001 Section 6.6 behavior that should lead to this error. The current tree has no per-key encrypted-packet accounting, no production-side local key update triggered by AEAD usage, and no connection-wide failed-authentication counter that closes with `AEAD_LIMIT_REACHED`.

This is a real compliance issue, not just an unused enum value.

## Standard Requirement

- `rfc9000:6817-6819`
  - `AEAD_LIMIT_REACHED` means the connection reached the AEAD confidentiality or integrity limit.
- `rfc9001:1794-1827`
  - endpoints MUST count encrypted packets for each set of keys;
  - endpoints MUST initiate a key update before exceeding the confidentiality limit;
  - if key update is not possible, endpoints MUST stop using the connection;
  - endpoints MUST count packets that fail authentication across the connection;
  - if the integrity limit is exceeded, endpoints MUST immediately close with `AEAD_LIMIT_REACHED`.

The requirement is therefore active enforcement, not just exposing wire code `0x0f`.

## Relevant Source Code

### Wire code exists, but no path maps to it

- `quiche/src/error.rs:172-200`

```rust
KeyUpdateError   = 0xe,
AeadLimitReached = 0xf,
...
Error::KeyUpdate => WireErrorCode::KeyUpdateError as u64,
_ => WireErrorCode::ProtocolViolation as u64,
```

`AeadLimitReached` is defined, but `Error::to_wire()` never emits it.

### Production key update is peer-driven only

- `quiche/src/lib.rs:3277-3308`

```rust
if self.handshake_confirmed &&
    hdr.ty != Type::ZeroRTT &&
    hdr.key_phase != self.key_phase
```

This path reacts to a peer flipping `key_phase`. No production send-side threshold check was found that proactively rotates keys before an AEAD limit.

- `quiche/src/test_utils.rs:366-392`

```rust
pub fn client_update_key(&mut self) -> Result<()> {
    ...
    self.client.key_phase = !self.client.key_phase;
}
```

The only local key-update initiator found is the test helper `Pipe::client_update_key()`.

### Send accounting is global, not per key

- `quiche/src/lib.rs:5466-5489`

```rust
self.next_pkt_num += 1;
...
self.sent_count += 1;
path.sent_count += 1;
```

The send path updates packet number and generic counters only. There is no per-key AEAD usage counter and no comparison against RFC 9001 confidentiality limits.

### Authentication failures are dropped, not accumulated

- `quiche/src/lib.rs:9338-9352`

```rust
if is_server && recv_count == 0 {
    return e;
}
...
Error::Done
```

After the connection has already processed packets, unauthenticated packet failures are converted to `Error::Done`.

- `quiche/src/lib.rs:2868-2886`

```rust
Err(Error::Done) => {
    ...
    left
},
Err(e) => {
    self.close(false, e.to_wire(), b"").ok();
    return Err(e);
},
```

Once the error has been changed to `Error::Done`, the receive loop does not close the connection. This is inconsistent with the RFC 9001 integrity-limit rule.

### Crypto backend failures stay generic

- `quiche/src/crypto/boringssl.rs:83-162`

`EVP_AEAD_CTX_open()` and `EVP_AEAD_CTX_seal_scatter()` failures become `Error::CryptoFail`. There is no translation to `AEAD_LIMIT_REACHED`.

## Runtime Evidence

### Commands run

```text
cargo test -p quiche update_key_request -- --nocapture
cargo test -p quiche invalid_packet -- --nocapture
```

### Results

```text
update_key_request*: 6 passed; 0 failed
invalid_packet*: 2 passed; 0 failed
```

### What the runs show

- `update_key_request*` passes only because the tests explicitly call `Pipe::client_update_key()`. That confirms local key update exists in tests, not as a production AEAD-limit trigger.
- `tests::invalid_packet` corrupts a post-handshake 1-RTT packet and still expects success:

```rust
buf[written - 1] = !buf[written - 1];
assert_eq!(pipe.server_recv(&mut buf[..written]), Ok(written));
```

- `quiche/src/tests.rs:4801-4806`

This matches `drop_pkt_on_err()` and shows that failed authentication is consumed and ignored instead of counted toward connection shutdown.

## Conclusion

RFC 9001 Section 6.6 requires active AEAD limit enforcement. `quiche` currently provides the wire error code, but not the required bookkeeping, proactive local key update, or integrity-limit close path. The issue is real.

## Impact

- On AEADs with meaningful confidentiality limits, long-lived connections can continue sending under one key generation without RFC-required proactive update behavior.
- Failed authentications are not counted across the lifetime of the connection.
- Even if a limit-related crypto failure occurred, it would not be surfaced as `AEAD_LIMIT_REACHED`.

## Fix Direction

- add per-key encrypted-packet counters to the active send key state;
- trigger local key update before the selected AEAD confidentiality limit;
- add a connection-wide failed-authentication counter across key generations;
- close with `AEAD_LIMIT_REACHED` when the integrity limit is exceeded;
- add an internal error path that maps to wire code `0x0f`.
