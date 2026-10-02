# Secondary zones lack refresh-timer countdown state

## Summary

Implementation detail: `Secondary` zone role exists, but no refresh timer/state-machine path was found.

This is a real partial issue: Hickory accepts and serves `Secondary` zones, but the visible server implementation does not maintain [RFC 1035](https://www.rfc-editor.org/rfc/rfc1035.html) refresh timers that count down with elapsed time.

## Standard Requirement

Official standard: [RFC 1035 Section 6.1.3](https://www.rfc-editor.org/rfc/rfc1035.html#section-6.1.3), "Time".

Key text:

```text
refresh timers and TTLs for cached data conceptually "count down"
```

Relevant context:

- [RFC 1035 Section 2.2](https://www.rfc-editor.org/rfc/rfc1035.html#section-2.2): secondary servers acquire zones and check primary updates via zone transfer.
- [RFC 1035 Section 3.3.13](https://www.rfc-editor.org/rfc/rfc1035.html#section-3.3.13): SOA `REFRESH`, `RETRY`, and `EXPIRE` define refresh, failed-refresh retry, and authority expiration intervals.
- [RFC 1035 Section 6.1.2](https://www.rfc-editor.org/rfc/rfc1035.html#section-6.1.2) and [RFC 1035 Section 6.1.3](https://www.rfc-editor.org/rfc/rfc1035.html#section-6.1.3): database timing includes per-zone refresh state; zone RR TTLs stay constant, while refresh timers/cached TTLs are converted against elapsed time.

Requirement extraction: when time elapses and refresh timers are maintained, the remaining refresh interval must decrease conceptually. It can be implemented with absolute timestamps; literal stored decrements are not required.

## Relevant Source Code

`crates/server/src/zone_handler/mod.rs:571-580` defines `Secondary`:

```rust
pub enum ZoneType {
    Primary,
    /// A secondary, i.e. replicated from the Primary
    Secondary,
    External,
}
```

`bin/src/config/mod.rs:353-389` loads `Primary` and `Secondary` through the same local store path:

```rust
ZoneTypeConfig::Primary(server_config) | ZoneTypeConfig::Secondary(server_config) => {
    let axfr_policy = server_config.axfr_policy();
    for store in &server_config.stores {
        /* creates FileZoneHandler or SqliteZoneHandler from local store config */
    }
}
```

`bin/src/config/mod.rs:537-557` shows `ServerZoneConfig` contains `axfr_policy`, DNSSEC fields, and `stores`; it has no primary-server list, next-refresh time, retry timer, expire timer, or refresh state field.

`crates/server/src/zone_handler/catalog.rs:772-808` treats `Primary | Secondary` as authoritative:

```rust
match handler.zone_type() {
    ZoneType::Primary | ZoneType::Secondary => build_authoritative_response(...).await,
    ZoneType::External => build_forwarded_response(...).await,
}
```

Targeted search in `bin/src` and `crates/server/src` found no `refresh_timer`, `next_refresh`, `refresh_interval`, `refresh_due`, `ZoneRefresh`, `expire_timer`, `retry_timer`, or secondary refresh scheduler. Hits for `retry`/`expire` are SOA fields, transport retry, or unrelated timeout handling.

## Implementation Behavior

`tests/test-data/test_configs/example_forwarder.toml:8-11` configures `0.0.127.in-addr.arpa` as:

```toml
[[zones]]
zone = "0.0.127.in-addr.arpa"
zone_type = "Secondary"
file = "default/127.0.0.1.zone"
```

At runtime, Hickory loads this as a `Secondary` file-backed zone and answers from `InMemoryZoneHandler`. The response path is authoritative, but it is a static local-zone path; no refresh timer is created, updated, or converted to a remaining interval.

## Inconsistency Reason

[RFC 1035](https://www.rfc-editor.org/rfc/rfc1035.html) ties secondary service to zone refresh and requires refresh timers to count down conceptually as time elapses. Hickory implements the `Secondary` label and authoritative serving, but the checked code path has no state for SOA refresh/retry/expire scheduling. Therefore the implementation is partial: the secondary zone surface exists, but RFC-style refresh-timer countdown behavior is missing or not proven reachable.

## Runtime Evidence

The recorded check first ran `cargo test -q -p hickory-dns example_forwarder -- --nocapture`, which passed. It then started the server with a file-backed Secondary zone for `0.0.127.in-addr.arpa.`; the startup output confirmed the Secondary configuration and three loaded records. A real UDP PTR query for `1.0.0.127.in-addr.arpa.` returned NoError, AA=true, RA=false, and one PTR answer to `localhost.` with TTL 259200. The server log matched that query. This establishes that the Secondary configuration is accepted and serves authoritative data. The absence of a refresh scheduler remains a source-inspection finding; this run did not observe refresh-timer countdown or a primary-server refresh exchange.

Recorded inputs, output, and checks:

```text
test result: ok. 1 passed; 0 failed
```

```text
zone: "0.0.127.in-addr.arpa",
zone_type_config: Secondary(...)
loading zone handlers for 0.0.127.in-addr.arpa. with stores [File(...)]
zone file loaded: 0.0.127.in-addr.arpa. with 3 records
```

```text
response id=0x4242 flags=0x8500 aa=True ra=False rcode=0 qd=1 an=1
answer[0] name=1.0.0.127.in-addr.arpa. type=PTR class=1 ttl=259200 ptr=localhost.
```

```text
performing name: 1.0.0.127.in-addr.arpa. type: PTR class: IN on zone handler 0.0.127.in-addr.arpa.
response:NoError rr:1/0/0 rflags:RD,AA
```

## Impact

An operator can configure a `Secondary` zone and receive authoritative answers, but the checked implementation behaves like a static local authoritative zone. It does not show the [RFC 1035](https://www.rfc-editor.org/rfc/rfc1035.html) secondary refresh lifecycle, so SOA refresh/retry/expire timing is not enforced on this path.

## Fix Direction

Add a secondary refresh manager with configured primary servers, SOA serial checks, AXFR/IXFR refresh, retry/expire scheduling, and stored absolute refresh times that are converted to remaining intervals when evaluated. Expired secondary data handling should be verified as a separate requirement.
