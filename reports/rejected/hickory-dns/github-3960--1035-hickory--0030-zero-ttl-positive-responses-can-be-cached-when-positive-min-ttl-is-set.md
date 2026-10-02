# Zero-TTL positive responses can be cached when `positive_min_ttl` is set

## Summary

[RFC 1035](https://www.rfc-editor.org/rfc/rfc1035.html) says an RR with TTL 0 must only be used for the current transaction and should not be cached. Hickory's default cache did not return a TTL 0 positive response in the runtime probe, but when `positive_min_ttl` is configured, Hickory raises the zero TTL to the configured minimum, inserts the response, and returns it from `ResponseCache`.

## Standard Requirement

Official standard: [RFC 1035 Section 3.2.1](https://www.rfc-editor.org/rfc/rfc1035.html#section-3.2.1)

Section: [RFC 1035](https://www.rfc-editor.org/rfc/rfc1035.html), 3.2.1, `TTL`; related standard text [RFC 1035 Section 4.1.3](https://www.rfc-editor.org/rfc/rfc1035.html#section-4.1.3)

```text
TTL             a 32 bit unsigned integer that specifies the time
                interval (in seconds) that the resource record may be
                cached before it should be discarded.  Zero values are
                interpreted to mean that the RR can only be used for the
                transaction in progress, and should not be cached.
```

Zero TTL is a no-cache signal for that RR; it should not be converted into a positive cache lifetime.

## Relevant Source Code

`implementions/hickory-dns-main/crates/resolver/src/config.rs:573-578`

```rust
/// Optional minimum TTL for positive responses.
///
/// If this is set, any positive responses with a TTL lower than this value will have a TTL of
/// `positive_min_ttl` instead. Otherwise, this will default to 0 seconds.
#[cfg_attr(feature = "serde", serde(with = "duration_opt"))]
pub positive_min_ttl: Option<Duration>,
```

`implementions/hickory-dns-main/crates/resolver/src/resolver.rs:558-563`

```rust
let cache = ResponseCache::new(
    context.options.cache_size,
    TtlConfig::from_opts(&context.options),
);
let client_cache =
    CachingClient::with_cache(cache, either, context.options.preserve_intermediates);
```

`implementions/hickory-dns-main/crates/resolver/src/cache.rs:370-378`

```rust
pub fn from_opts(opts: &config::ResolverOpts) -> Self {
    Self::from(TtlBounds {
        positive_min_ttl: opts.positive_min_ttl,
        negative_min_ttl: opts.negative_min_ttl,
        positive_max_ttl: opts.positive_max_ttl,
        negative_max_ttl: opts.negative_max_ttl,
    })
}
```

`implementions/hickory-dns-main/crates/resolver/src/cache.rs:139-148`

```rust
for record in message
    .answers
    .iter_mut()
    .chain(message.authorities.iter_mut())
    .chain(message.additionals.iter_mut())
{
    let (min_secs, max_secs) = self
        .ttl_config
        .positive_ttl_bounds_secs(record.record_type());
    record.ttl = record.ttl.clamp(min_secs, max_secs);
}
```

`implementions/hickory-dns-main/crates/resolver/src/cache.rs:180-182`

```rust
let ttl = min_ttl
    .unwrap_or(positive_min_ttl)
    .clamp(positive_min_ttl, positive_max_ttl);
```

`implementions/hickory-dns-main/crates/resolver/src/cache.rs:69-91`

```rust
let (ttl, result) = match result {
    Ok(mut message) => {
        let ttl = self.clamp_positive_ttls(query.query_type, &mut message);
        (ttl, Ok(message))
    }
    Err(NetError::Dns(DnsError::NoRecordsFound(no_records))) => {
        /* negative-cache path omitted */
    }
    Err(_) => return,
};
let valid_until = now + ttl;
```

`implementions/hickory-dns-main/crates/resolver/src/cache.rs:101-108`

```rust
self.cache.insert(
    query,
    Entry {
        result: Arc::new(result),
        original_time: now,
        valid_until,
    },
);
```

## Implementation Behavior

With default TTL bounds, `positive_min_ttl` is absent and the probe observed no cached result for a zero-TTL positive response.

With `positive_min_ttl = 5s`, the same zero-TTL A record is changed by `record.ttl.clamp(min_secs, max_secs)` from `0` to `5`, the cache lifetime is also clamped to `5s`, and `ResponseCache::get` returns the cached response.

## Inconsistency Reason

[RFC 1035](https://www.rfc-editor.org/rfc/rfc1035.html) requires a zero-TTL RR to be used only for the current transaction and not cached. Hickory treats TTL 0 as a positive response below the configured minimum TTL. That is acceptable for nonzero low TTLs, but not for TTL 0, because TTL 0 has a distinct no-cache meaning in the standard.

The runtime probe showed default cache lookup returns `None`. The affected path requires a configured `positive_min_ttl`.

## Runtime Evidence

The recorded Rust probe ran with `cargo run --quiet` and inserted the same positive response with TTL 0 into ResponseCache under default TTL settings and under `positive_min_ttl=5s`. It queried each cache at controlled elapsed times. The default cache returned None both at the insertion instant and one nanosecond later. The configured cache returned TTL 5 at the insertion instant and TTL 4 after one second. The result demonstrates a configuration-dependent issue: raising the minimum TTL makes a zero-TTL response reusable, while the default control did not return it.

Recorded inputs, output, and checks:

```text
default_same_instant=None
default_after_1ns=None
positive_min_5s_same_instant=Some(5)
positive_min_5s_after_1s=Some(4)
```

## Impact

Resolvers configured with `positive_min_ttl` can reuse records that an authoritative response explicitly marked as transaction-only. This can serve stale data for the configured minimum TTL.

## Fix Direction

Do not apply `positive_min_ttl` to RR TTL 0. If the positive cache lifetime is derived from a zero-TTL answer, skip inserting that response into `ResponseCache`, or keep zero-TTL records out of cached responses instead of raising them to the configured minimum.
