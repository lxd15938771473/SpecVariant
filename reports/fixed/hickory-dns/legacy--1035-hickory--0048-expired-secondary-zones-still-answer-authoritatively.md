# Expired secondary zones still answer authoritatively

## Summary

[RFC 1035](https://www.rfc-editor.org/rfc/rfc1035.html) says that if a name server cannot refresh a zone within its expiration parameter, it should answer as if it does not possess that zone. Hickory exposes `ZoneType::Secondary`, but the visible query path treats `Primary` and `Secondary` the same: both enter the authoritative response builder. A focused run with a secondary zone whose SOA `expire=1` still returns `NoError`, `authoritative=true`, and one answer after waiting longer than the expire interval.

## Standard Requirement

Official standard: [RFC 1035 Section 6.3](https://www.rfc-editor.org/rfc/rfc1035.html#section-6.3), Zone refresh and reload processing.

```text
unable to refresh a
zone within the its expiration parameter.

should answer queries as if it were not supposed to possess the zone.
```

In context, this applies when a zone refresh has failed or cannot complete before the SOA expiration limit. Such a secondary should stop answering as authoritative for that zone.

## Relevant Source Code

`implementions/hickory-dns-main/bin/src/config/mod.rs:353`

```rust
match self.zone_type_config {
    ZoneTypeConfig::Primary(server_config) | ZoneTypeConfig::Secondary(server_config) => {
        debug!(
            "loading zone handlers for {zone_name} with stores {:?}",
            server_config.stores
        );
```

Primary and secondary zones use the same store-loading path.

`implementions/hickory-dns-main/bin/src/config/mod.rs:495`

```rust
fn zone_type(&self) -> ZoneType {
    match &self.zone_type_config {
        ZoneTypeConfig::Primary { .. } => ZoneType::Primary,
        ZoneTypeConfig::Secondary { .. } => ZoneType::Secondary,
        ZoneTypeConfig::External { .. } => ZoneType::External,
    }
}
```

The configuration can create a real `ZoneType::Secondary`.

`implementions/hickory-dns-main/crates/server/src/zone_handler/mod.rs:573`

```rust
pub enum ZoneType {
    /// This authority for a zone
    Primary,
    /// A secondary, i.e. replicated from the Primary
    Secondary,
    /// A cached zone that queries other nameservers
    External,
}
```

`implementions/hickory-dns-main/crates/server/src/zone_handler/catalog.rs:773`

```rust
match handler.zone_type() {
    ZoneType::Primary | ZoneType::Secondary => {
        build_authoritative_response(
            result,
            handler,
            request_meta,
            lookup_options,
            request_id,
            query,
        )
        .await
    }
```

`implementions/hickory-dns-main/crates/server/src/zone_handler/catalog.rs:808`

```rust
let mut response_meta = Metadata::response_from_request(request_meta);
response_meta.authoritative = true;
```

Secondary zones enter the same authoritative response path and get the AA bit set.

`implementions/hickory-dns-main/crates/server/src/store/in_memory/mod.rs:335`

```rust
async fn lookup(
    &self,
    name: &LowerName,
    mut query_type: RecordType,
    _request_info: Option<&RequestInfo<'_>>,
    lookup_options: LookupOptions,
) -> LookupControlFlow<AuthLookup> {
    let inner = self.inner.read().await;

    if query_type == RecordType::AXFR {
        return Break(Err(LookupError::NetError(
            "AXFR must be handled with ZoneHandler::zone_transfer()".into(),
        )));
    }

    if query_type == RecordType::ANY {
        query_type = inner.replace_any(name);
    }

    let answer = inner.inner_lookup(name, query_type, lookup_options);
```

Lookup reads records directly; this path does not check SOA `expire`, refresh state, or current time before returning data.

## Implementation Behavior

Hickory can load a `Secondary` zone, but the response path treats it as authoritative whenever records exist. The server-side source search for refresh-state terms found only unrelated TLS/TSIG expiration messages, not a secondary refresh/expire suppression path.

## Inconsistency Reason

[RFC 1035](https://www.rfc-editor.org/rfc/rfc1035.html) requires a zone that could not be refreshed within its expiration parameter to be answered as not possessed. Hickory's `Secondary` role has no visible expire gate in lookup, and `Catalog` explicitly groups `Primary | Secondary` into the authoritative response builder. Therefore an expired secondary can continue to return authoritative answers.

## Runtime Evidence

The recorded Rust probe created an in-memory `ZoneType::Secondary` with SOA expire=1 second and an A record for `www.expired.test.`, then queried before and after a two-second wait. It ran with `cargo run --quiet`. Both responses were NoError with authoritative=true and one A answer at TTL 1. The post-expiration response still served the zone authoritatively. The probe completed with exit code 0. This directly tests the in-memory secondary response path after elapsed time, without demonstrating a complete primary/secondary refresh cycle.

Recorded inputs, output, and checks:

```text
before-expire: rcode=NoError authoritative=true answers=1
before-expire: answer name=www.expired.test. type=A ttl=1
after-expire: rcode=NoError authoritative=true answers=1
after-expire: answer name=www.expired.test. type=A ttl=1
exit=0
```

## Impact

A configured secondary zone can keep serving stale zone data authoritatively after the SOA expiration interval. This affects secondary-zone deployments; it does not apply to ordinary primary zones.

## Fix Direction

Track secondary refresh state and last successful refresh time. Before answering for a secondary zone, compare that state against the SOA `expire` interval; if the zone is expired, suppress authoritative answers and respond as if the zone is not configured or not authoritative.
