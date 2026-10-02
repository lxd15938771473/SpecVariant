# RFC9846 mbedTLS issue: TLS 1.3 PSK heap copies are freed without zeroization

## Verdict

- Verdict: `issue_found`
- Confidence: `high`
- Covered Record IDs: `cand-6e313ff09513-baseline`, `cand-f4aa67774bbb-replay`, `cand-137e77264f4e-state-order`, `cand-966ac06e9df8-missing`
- Root Cause Key: `req-bbcb04778e8d4e3b0b92`

## Summary

This candidate was originally recorded as `suspected_issue` because the earlier report suspected insufficient long-term protection for retained or external key-share material. The recheck narrows and corrects the finding. The previously suspected external `xxdh_psa_privkey` path is not confirmed; the real issue is in the TLS 1.3 PSK path, where mbedTLS exports a plaintext PSK heap copy and then releases that copy with ordinary `mbedtls_free()` after use, without first zeroizing it.

The candidate should therefore be upgraded from `suspected_issue` to `issue_found`, but the root cause should be described as plaintext PSK-copy lifetime rather than external key-share retention.

## Standard Check

Official standard references:

- [RFC 9846, Section 7.1, Key Schedule](https://www.rfc-editor.org/rfc/rfc9846.html#section-7.1): PSKs and asymmetric shared secrets are TLS 1.3 key-schedule input secrets, and once all values derived from a given secret have been computed, that secret `SHOULD` be erased.
- [RFC 9846, Appendix F, Overview of Security Properties](https://www.rfc-editor.org/rfc/rfc9846.html#appendix-F): external and resumption PSKs are long-term keying material; failing to erase ephemeral or connection-specific keys or secrets can create additional long-term keys and can later compromise protection for the connection's traffic.

Normative-strength correction: Appendix F's "must be protected" wording is lowercase and is not a BCP 14 `MUST`. This report treats the issue as a Section 7.1 `SHOULD be erased` key-lifecycle gap, with Appendix F explaining the security consequence.

## Code Check

The previously suspected external key-share path is not the confirmed defect:

- `implementions/mbedtls-development/library/ssl_tls13_keys.c:1467` destroys the internally generated TLS 1.3 PSA ECDHE private key.
- `implementions/mbedtls-development/library/ssl_tls13_keys.c:1500`-`:1501` uses `mbedtls_zeroize_and_free(shared_secret, shared_secret_len)` to wipe and release the TLS 1.3 shared secret.

The confirmed defect is in the TLS 1.3 PSK path:

- `implementions/mbedtls-development/library/ssl_tls13_keys.c:1244` calls `mbedtls_ssl_tls13_export_handshake_psk()` to export the handshake PSK.
- `implementions/mbedtls-development/library/ssl_tls13_keys.c:1253`-`:1254` uses that PSK to derive the early secret.
- `implementions/mbedtls-development/library/ssl_tls13_keys.c:1256` calls `mbedtls_free((void *) psk)` to release the PSK without prior zeroization.
- `implementions/mbedtls-development/library/ssl_tls13_server.c:429`-`:438` has the same ordinary-free pattern after exporting a PSK on the server binder verification path.
- `implementions/mbedtls-development/tf-psa-crypto/platform/platform_util.c:123`-`:129` already provides `mbedtls_zeroize_and_free()`.
- `implementions/mbedtls-development/tf-psa-crypto/include/mbedtls/platform.h:149`-`:152` shows that, in the default configuration, `mbedtls_free` maps to ordinary `free`.

## Runtime Evidence

A focused TLS 1.3 PSK handshake probe was run during the third-pass recheck on 2026-08-10. The probe exercised a real client/server PSK handshake and instrumented release behavior so it could distinguish buffers wiped before free from plaintext buffers released directly.

Observed output:

```text
positive_control_zeroized_before_free=yes
handshake_client_done=yes
handshake_server_done=yes
handshake_iterations=2
negotiated_version=TLSv1.3
negotiated_ciphersuite=TLS1-3-CHACHA20-POLY1305-SHA256
plaintext_psk_free_count_during_handshake=3
plaintext_psk_free_1_size=32
plaintext_psk_free_2_size=32
plaintext_psk_free_3_size=32
tracking_overflow_count=0
result=confirmed_real_tls13_psk_handshake_frees_plaintext_psk_without_zeroization
```

Interpretation:

- `positive_control_zeroized_before_free=yes` shows that the probe can distinguish "zeroized before free" from "freed without zeroization".
- The client and server completed a TLS 1.3 PSK handshake in two iterations and negotiated `TLS1-3-CHACHA20-POLY1305-SHA256`.
- During that real handshake, the probe observed three 32-byte plaintext PSK buffers reaching ordinary `free` before zeroization.
- `tracking_overflow_count=0` shows that the observation buffer did not overflow during the run.

## Decision Reason

RFC 9846 Section 7.1 says that once all values derived from a given key-schedule secret have been computed, that secret `SHOULD` be erased. mbedTLS exports plaintext PSK heap copies on the TLS 1.3 PSK path, uses them to derive key-schedule material, and then releases them through ordinary `mbedtls_free()` without wiping. The real TLS 1.3 PSK handshake probe confirms that this behavior occurs during an actual handshake. The final classification is therefore `issue_found`.

## Suggested Fix

- Replace the ordinary release at `implementions/mbedtls-development/library/ssl_tls13_keys.c:1256` with `mbedtls_zeroize_and_free()` using the known `psk_len`.
- Replace the ordinary release at `implementions/mbedtls-development/library/ssl_tls13_server.c:438` with the same zeroize-and-free pattern.
- In `mbedtls_ssl_tls13_export_handshake_psk()` failure cleanup, retain the allocated length and wipe any allocated plaintext PSK buffer before releasing it.
