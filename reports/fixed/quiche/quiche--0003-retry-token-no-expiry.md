# Retry token has no short validity window

## Standard Requirement

RFC 9000 `2460-2462`:

> Servers SHOULD ensure that tokens sent in Retry packets are only accepted for a short time, as they are returned immediately by clients.

## Relevant Source Code

- `tokio-quiche/src/quic/addr_validation_token.rs:55-110` stores only `IP` and `original_dcid` under HMAC. There is no timestamp, expiry field, or lifetime check.
- `tokio-quiche/src/quic/mod.rs:306-324` creates one `AddrValidationTokenManager` for the listener via `Default::default()`. A token stays acceptable as long as that in-memory signing key remains in use.

## Runtime Evidence

See `runtime/manual-token-recheck/retry-token-runtime-evidence.md`.

- `cargo test --package tokio-quiche validate -- --nocapture`: 4 `addr_validation_token` validation tests passed.
- `python opt/runs/rfc9000-quiche/501-1000/runtime/source_probe.py reproducer --workspace . --out opt/runs/rfc9000-quiche/501-1000/runtime/source_probe_results.json`: `retry_token_no_expiry = true`.

## Conclusion

A minted Retry token remains acceptable until the listener key changes. This is distinct from `0002`, but it directly contributes to the replay risk.
