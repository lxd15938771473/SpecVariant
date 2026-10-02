# TLS 1.3 transform setup can leave an imported traffic key undisposed on failure

## Summary

This is a real issue, but the scope is narrower than a broad TLS 1.3 key-erasure failure. Normal mbedTLS session and transform free paths do erase or destroy the checked key material. The confirmed problem is the partial transform setup path: if `mbedtls_ssl_tls13_populate_transform()` imports the encryption PSA key successfully and then fails while importing the decryption PSA key, the already imported encryption key is not destroyed. The callers then free the local transform allocation directly, bypassing `mbedtls_ssl_transform_free()`.

## Standard Check

RFC 9846 supports this as a key-lifetime/security cleanup issue, but not as an uppercase `MUST` violation.

Official standard references:

- [RFC 9846, Section 1.1, Conventions and Terminology](https://www.rfc-editor.org/rfc/rfc9846.html#section-1.1): BCP 14 keywords are normative only when they appear in all capitals. Appendix F's lower-case "need to be erased" wording is therefore not a BCP 14 `MUST`.
- [RFC 9846, Section 7.1, Key Schedule](https://www.rfc-editor.org/rfc/rfc9846.html#section-7.1): once all values derived from a given secret have been computed, that secret `SHOULD` be erased.
- [RFC 9846, Section 7.2, Updating Traffic Secrets](https://www.rfc-editor.org/rfc/rfc9846.html#section-7.2): once the next-generation application traffic secret and associated traffic keys have been computed, implementations `SHOULD delete` the previous application traffic secret and its associated traffic keys.
- [RFC 9846, Section 7.3, Traffic Key Calculation](https://www.rfc-editor.org/rfc/rfc9846.html#section-7.3): traffic keying material such as `[sender]_write_key` is derived from the traffic secret.
- [RFC 9846, Appendix F, Overview of Security Properties](https://www.rfc-editor.org/rfc/rfc9846.html#appendix-F): erasure of session keys and key-schedule-derived material is part of the forward-secrecy security property, while the relevant wording is lower-case rather than BCP 14 `MUST`.

The precise requirement is therefore: connection-specific TLS 1.3 traffic key material should not have its lifetime extended after it is no longer usable. After transform setup fails, the partially imported PSA traffic key has no valid connection owner and should be destroyed.

## Implementation Check

In `mbedtls_ssl_tls13_populate_transform()`, the encryption key is imported first:

```c
/* implementions/mbedtls-development/library/ssl_tls13_keys.c:1020 */
if ((status = psa_import_key(&attributes,
                             key_enc,
                             PSA_BITS_TO_BYTES(key_bits),
                             &transform->psa_key_enc)) != PSA_SUCCESS) {
    return PSA_TO_MBEDTLS_ERR(status);
}
```

The decrypt key is imported second. If this import fails, the function returns without destroying `transform->psa_key_enc`:

```c
/* implementions/mbedtls-development/library/ssl_tls13_keys.c:1031 */
if ((status = psa_import_key(&attributes,
                             key_dec,
                             PSA_BITS_TO_BYTES(key_bits),
                             &transform->psa_key_dec)) != PSA_SUCCESS) {
    return PSA_TO_MBEDTLS_ERR(status);
}
```

The TLS 1.3 callers zeroize the temporary `traffic_keys` buffer, but directly free the partially populated transform on error:

```c
/* implementions/mbedtls-development/library/ssl_tls13_keys.c:1218 */
mbedtls_platform_zeroize(&traffic_keys, sizeof(traffic_keys));
if (ret != 0) {
    mbedtls_free(transform_earlydata);
}
```

```c
/* implementions/mbedtls-development/library/ssl_tls13_keys.c:1689 */
mbedtls_platform_zeroize(&traffic_keys, sizeof(traffic_keys));
if (ret != 0) {
    mbedtls_free(transform_handshake);
}
```

```c
/* implementions/mbedtls-development/library/ssl_tls13_keys.c:1783 */
mbedtls_platform_zeroize(&traffic_keys, sizeof(traffic_keys));
if (ret != 0) {
    mbedtls_free(transform_application);
}
```

The normal transform destructor would destroy both PSA keys and zeroize the transform:

```c
/* implementions/mbedtls-development/library/ssl_msg.c:6090 */
psa_destroy_key(transform->psa_key_enc);
psa_destroy_key(transform->psa_key_dec);
mbedtls_platform_zeroize(transform, sizeof(mbedtls_ssl_transform));
```

The PSA API documentation confirms that `psa_destroy_key()` is the operation that destroys key material and frees associated key resources (`implementions/mbedtls-development/tf-psa-crypto/include/psa/crypto.h:533`-`540`). The PSA transition guide also states that a key created with `psa_import_key()` should finally be destroyed and consumes a key store entry until destruction or application exit (`implementions/mbedtls-development/tf-psa-crypto/docs/psa-transition.md:103`-`111`).

## Runtime Evidence

The real-key persistence probe was rebuilt and run during the recheck. It wraps `psa_import_key()` so that the first call goes to the real PSA implementation and the second call fails with `PSA_ERROR_INSUFFICIENT_MEMORY`. It then frees the transform as the callers do and checks whether the first PSA key still exists.

Run result:

```text
compile_exit=0
ret=-141 import_call_count=2 destroy_before_cleanup=0 enc_key_id=1073741824 lookup_after_return=0 manual_cleanup=0
probe_exit=0
```

Interpretation:

- `compile_exit=0`: the focused probe rebuilt successfully.
- `import_call_count=2`: the transform path attempted both PSA imports.
- `ret=-141`: the injected second import failure propagated out of `mbedtls_ssl_tls13_populate_transform()`.
- `destroy_before_cleanup=0`: no `psa_destroy_key()` call occurred before the caller-style direct transform free.
- `lookup_after_return=0`: `psa_get_key_attributes(saved_key)` returned `PSA_SUCCESS`, proving the first real PSA key still existed after the transform was directly freed.
- `manual_cleanup=0`: the probe's final manual `psa_destroy_key(saved_key)` succeeded, confirming the residual object was still a live PSA key.
- `probe_exit=0`: the probe considered the issue reproduced.

Control run:

```text
app_secrets_zero_after_session_free=1 sizeof_app_secrets=256
resumption_key_zero_after_session_free=1
transform_zero_after_free=1 sizeof_transform=240
control_exit=0
```

The control confirms that normal explicit session and transform free paths erase the checked key material. The confirmed issue is limited to the partial TLS 1.3 transform population failure path.

## Impact

The affected path requires a failure during the second PSA key import after the first import has succeeded, for example due to PSA allocation or resource exhaustion. In that case, a TLS 1.3 traffic key imported into PSA can remain undisposed after handshake or transform setup failure. Depending on the PSA backend, this can leak a transient key slot and extend the lifetime of connection-specific traffic key material beyond the failed connection setup.

Practical severity is likely low to medium because the path requires an internal resource/failure condition, but it is a real cleanup defect for sensitive key material.

## Fix Direction

Prefer central cleanup inside `mbedtls_ssl_tls13_populate_transform()`:

- Track successful PSA imports.
- On any later failure, destroy already imported PSA keys and zeroize the partial transform, or call a cleanup helper that is safe for partially initialized transforms.

Changing each TLS 1.3 caller to call `mbedtls_ssl_transform_free()` before `mbedtls_free()` would also address current callers, but central cleanup is safer for future call sites.
