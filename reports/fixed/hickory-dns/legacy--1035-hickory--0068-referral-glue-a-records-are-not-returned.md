# Referral glue A records are not returned

## Summary

[RFC 1035](https://www.rfc-editor.org/rfc/rfc1035.html) requires NS records used in referrals to trigger a special search for glue in the zone where the NS RR resides. Hickory returns the delegation `NS` RRset, but does not return the in-zone glue `A` record for the referred name server.

## Standard Requirement

Official standard: [RFC 1035 Section 3.3.11](https://www.rfc-editor.org/rfc/rfc1035.html#section-3.3.11).

```text
NS records cause both the usual additional section processing to locate
a type A record, and, when used in a referral, a special search of the
zone in which they reside for glue information.
```

This means a referral containing an `NS` RRset should search the parent zone that owns the delegation for the name server's glue `A` record.

## Relevant Source Code

`implementions/hickory-dns-main/crates/server/src/store/in_memory/inner.rs:197-218` returns a delegation `NS` RRset before looking up the requested name/type:

```rust
pub(super) fn inner_lookup(
    &self,
    name: &LowerName,
    record_type: RecordType,
    lookup_options: LookupOptions,
) -> Option<Arc<RecordSet>> {
    let mut search_name = name.clone();
    while !search_name.is_root() {
        let ns_key = RrKey::new(search_name.clone(), RecordType::NS);
        let soa_key = RrKey::new(search_name.clone(), RecordType::SOA);
        let ns_rrset = self.records.get(&ns_key);
        let has_soa = self.records.contains_key(&soa_key);
        let ds_exact = record_type == RecordType::DS && search_name == *name;

        match (ns_rrset, has_soa) {
            (Some(_), false) if ds_exact => {}
            (Some(ns), false) => return Some(ns.clone()),
            (Some(_), true) => break,
            (None, _) => {}
        }
```

`implementions/hickory-dns-main/crates/server/src/store/in_memory/mod.rs:374-383` only performs additional processing when `maybe_next_name(answer, query_type)` returns a target:

```rust
let additionals_root_chain_type: Option<(_, _)> = answer
    .as_ref()
    .and_then(|a| maybe_next_name(a, query_type))
    .and_then(|(search_name, search_type)| {
        inner
            .additional_search(name, query_type, search_name, search_type, lookup_options)
            .map(|adds| (adds, search_type))
    });
```

`implementions/hickory-dns-main/crates/server/src/store/in_memory/mod.rs:692-708` does not treat `NS` returned by an `A` query as an additional-search trigger:

```rust
fn maybe_next_name(record_set: &RecordSet, query_type: RecordType) -> Option<(LowerName, RecordType)> {
    let t = match (record_set.record_type(), query_type) {
        (t @ RecordType::ANAME, RecordType::A)
        | (t @ RecordType::ANAME, RecordType::AAAA)
        | (t @ RecordType::ANAME, RecordType::ANAME) => t,
        (t @ RecordType::NS, RecordType::NS) => t,
        (t @ RecordType::MX, RecordType::MX) => t,
        (t @ RecordType::SRV, RecordType::SRV) => t,
        _ => return None,
    };
```

`implementions/hickory-dns-main/crates/server/src/zone_handler/catalog.rs:990-1004` moves referral `NS` records into Authority, but does not add glue after referral classification:

```rust
if let Some(mut lookup_records) = answers {
    if let Some(adds) = lookup_records.take_additionals() {
        message.additionals.extend(adds.iter().cloned());
    }

    let is_referral = lookup_records.iter().next().is_some_and(|r| {
        r.record_type() == RecordType::NS
            && query.query_type() != RecordType::NS
            && query.query_type() != RecordType::ANY
    });

    if is_referral {
        message.authorities.extend(lookup_records.iter().cloned());
```

## Implementation Behavior

For a query below a delegated child, `inner_lookup` returns the child's `NS` RRset as a referral. Because the original query type is `A`, `maybe_next_name(NS, A)` returns `None`, so no glue lookup is attached before the catalog builds the referral response. Even a direct `NS` query does not find the glue `A`: the additional lookup for `ns.child.example.test. A` is intercepted by the same delegation check and returns `child.example.test. NS` instead.

## Inconsistency Reason

The standard requires a special search in the zone where the referral `NS` resides. Hickory uses its normal lookup path for additional data, and that path stops at the delegation before reaching glue data below the zone cut. As a result, a parent zone can contain the required glue `A` record, but the referral response still omits it.

## Runtime Evidence

The recorded Rust probe ran with `cargo run --quiet` against a zone containing delegation `child.example.test. NS` and glue `ns.child.example.test. A`. It compared a direct child NS query with a child A query that produced a referral, and inspected Answer, Authority, and Additional. The direct query returned NS but no glue A. The referral had NS in Authority and an empty Additional section even though the glue A existed in the zone. Exit code 2 was the probe's reported issue result. The output below records the assembled response sections and the explicit glue checks.

Recorded inputs, output, and checks:

```text
direct child NS query:
  answers=child.example.test. NS
  authorities=[]
  additionals=child.example.test. NS
referral child A query:
  answers=[]
  authorities=child.example.test. NS
  additionals=[]
direct_has_ns=true
direct_has_glue_a=false
referral_has_ns=true
referral_has_glue_a=false
RESULT: referral omits in-zone glue A
exit=2
```

## Impact

Resolvers may receive referrals without in-bailiwick glue and need extra lookups to continue resolution. In some configurations this can increase latency or make delegation resolution fail.

## Fix Direction

When returning a referral, perform a glue-specific lookup in the parent zone for each referral `NSDNAME`. That lookup must be able to read in-zone glue below the delegation point instead of being stopped by the normal delegation check. Add the found `A` records to Additional.
