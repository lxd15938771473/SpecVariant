# AXFR Can Mix Old And New Zone Versions

## Summary

[RFC 1035](https://www.rfc-editor.org/rfc/rfc1035.html) requires an AXFR in progress to continue sending the old zone version when possible, and never send part of one version and part of another. Hickory builds an AXFR from separately-read `start_soa`, body records, and `end_soa`, so a concurrent update can produce a mixed-version transfer.

## Standard Requirement

- Standard: [RFC 1035 Section 6.3](https://www.rfc-editor.org/rfc/rfc1035.html#section-6.3), "Zone refresh and reload processing"
- Official link: [RFC 1035 Section 6.3](https://www.rfc-editor.org/rfc/rfc1035.html#section-6.3)
- Standard reference: [RFC 1035 Section 6.3](https://www.rfc-editor.org/rfc/rfc1035.html#section-6.3)

```text
If a master is sending a zone out via AXFR, and a new version is created
during the transfer, the master should continue to send the old version
if possible.  In any case, it should never send part of one version and
part of another.  If completion is not possible, the master should reset
the connection on which the zone transfer is taking place.
```

The key invariant is transfer-wide version consistency: one AXFR must not contain records from both the old and new zone versions.

## Relevant Source Code

Path base: `implementions/hickory-dns-main`.

`crates/server/src/store/in_memory/mod.rs:519-568` builds AXFR from three separate reads:

```rust
let future = self.lookup(self.origin(), RecordType::SOA, None, lookup_options);
let start_soa = if let LookupControlFlow::Continue(Ok(res)) = future.await {
    res.unwrap_records()
} else {
    LookupRecords::Empty
};

let future = self.lookup(
    self.origin(),
    RecordType::SOA,
    None,
    LookupOptions::default(),
);
let end_soa = if let LookupControlFlow::Continue(Ok(res)) = future.await {
    res.unwrap_records()
} else {
    LookupRecords::Empty
};

let records = AxfrRecords::new(
    lookup_options.dnssec_ok,
    self.inner.read().await.records.values().cloned().collect(),
);

Some((
    Ok(ZoneTransfer {
        start_soa,
        records,
        end_soa,
    }),
    None,
))
```

`crates/server/src/zone_handler/auth_lookup.rs:168-180,237-253,347-367` shows the transfer stores `Arc<RecordSet>` references, not a deep-copied immutable zone snapshot:

```rust
pub struct AxfrRecords {
    dnssec_ok: bool,
    rrsets: Vec<Arc<RecordSet>>,
}

pub enum LookupRecords {
    Records {
        lookup_options: LookupOptions,
        records: Arc<RecordSet>,
    },
    ManyRecords(LookupOptions, Vec<Arc<RecordSet>>),
    Section(Vec<Record>),
}

pub struct ZoneTransfer {
    pub start_soa: LookupRecords,
    pub records: AxfrRecords,
    pub end_soa: LookupRecords,
}
```

`crates/server/src/store/sqlite/mod.rs:718-732,791-804,916-943,1122-1143` shows updates can mutate the in-memory zone and increment the SOA serial while AXFR delegates to the in-memory transfer path:

```rust
pub async fn update_records(
    &self,
    records: &[Record],
    auto_signing_and_increment: bool,
) -> Result<bool, ResponseCode> {
    let mut updated = false;
    let serial: u32 = self.in_memory.serial().await;
    ...
    let upserted = self.in_memory.upsert(rr.clone(), serial).await;
    updated = upserted || updated
    ...
    self.in_memory.increment_soa_serial().await
}

let (zone_transfer, _) = self
    .in_memory
    .zone_transfer(request, lookup_options, now)
    .await?;
```

## Implementation Behavior

The AXFR construction does not hold one read lock or clone a complete zone version for the whole transfer. It can read `start_soa` from the old version, then observe body records and `end_soa` after a concurrent update has inserted records and incremented the SOA serial.

## Inconsistency Reason

[RFC 1035](https://www.rfc-editor.org/rfc/rfc1035.html) forbids mixing two zone versions in one AXFR. Hickory has no transfer-wide snapshot and no branch that detects this inconsistency and resets the connection. Therefore an update between the independent reads can make the emitted transfer contain both old and new version data.

## Runtime Evidence

The recorded Rust probe ran with `cargo run --quiet` and used a SqliteZoneHandler to perform the separate SOA/body/SOA reads used to model AXFR assembly. The control reads all saw serial 1 and no newly added record. It then read the starting SOA, applied an update adding `new.example.com. A 192.0.2.99`, and read the body and ending SOA. The update succeeded; the resulting view mixed starting serial 1 with body/end serial 2 and contained the new record. A final control read saw serial 2 throughout. This demonstrates a mixed view under an explicitly interleaved update through the separate-read APIs. It is an AXFR-style simulation, not an observed concurrent network transfer.

Recorded inputs, output, and checks:

```text
control_start_serial=1
control_body_serial=1
control_end_serial=1
control_has_new_rr=false
update_applied=true
mixed_start_serial=1
mixed_body_serial=2
mixed_end_serial=2
mixed_has_new_rr=true
final_start_serial=2
final_body_serial=2
final_end_serial=2
final_has_new_rr=true
```

## Impact

A secondary receiving such an AXFR can load a zone image that never existed as a single authoritative version.

## Fix Direction

Build AXFR from one immutable zone snapshot: hold one read guard while cloning all records needed for `start_soa`, body, and `end_soa`, or introduce explicit zone-version snapshots. If a consistent version cannot be retained during transfer, abort/reset the transfer instead of returning a normal mixed response.
