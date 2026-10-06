# Retry token replay is not limited

## Standard Requirement

RFC 9000 `2458-2465`:

> Attackers could replay tokens to use servers as amplifiers in DDoS attacks. To protect against such attacks, servers MUST ensure that replay of tokens is prevented or limited.

Retry tokens are also expected to be returned immediately and accepted only for a short time.

## Relevant Source Code

- `tokio-quiche/src/quic/addr_validation_token.rs:55-110` generates `HMAC(IP || original_dcid)` and validates only HMAC plus IP binding. There is no nonce, replay cache, consume-on-use, or other replay-limiting state.
- `tokio-quiche/src/quic/router/acceptor.rs:241-259` accepts any non-empty token that passes `validate_and_extract_original_dcid()`.
- `quiche/src/lib.rs:2025-2026` marks the peer address as verified once Retry validation succeeds, so a replayed valid Retry token bypasses the pre-validation anti-amplification gate.

## Runtime Evidence

See `runtime/manual-token-recheck/retry-token-runtime-evidence.md`.

- `cargo test --package tokio-quiche validate -- --nocapture`: 4 `addr_validation_token` validation tests passed.
- `python opt/runs/rfc9000-quiche/501-1000/runtime/source_probe.py reproducer --workspace . --out opt/runs/rfc9000-quiche/501-1000/runtime/source_probe_results.json`: `token_replay_not_limited = true`.

## Conclusion

The implementation validates a stateless Retry token but does not prevent reuse and does not bound reuse with any replay-limiting mechanism. This is a real issue.
