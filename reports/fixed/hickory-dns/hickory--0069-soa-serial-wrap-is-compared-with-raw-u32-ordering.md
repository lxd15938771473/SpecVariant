# SOA serial wrap is compared with raw `u32` ordering

## Summary

Hickory has a `SerialNumber` helper that implements sequence-space comparison, but the reachable SOA replacement path in `RecordSet::insert` compares SOA serials with ordinary `u32 <=`. A wrapped update from `4294967295` to `0` is therefore rejected.

## Standard Requirement

Official standard: [RFC 1035 Section 3.3.13](https://www.rfc-editor.org/rfc/rfc1035.html#section-3.3.13)

Section: [RFC 1035 Section 3.3.13](https://www.rfc-editor.org/rfc/rfc1035.html#section-3.3.13), SOA RDATA format.

```text
SERIAL          The unsigned 32 bit version number of the original copy
                of the zone.  Zone transfers preserve this value.  This
                value wraps and should be compared using sequence space
                arithmetic.
```

The serial is a wrapping 32-bit value. After `u32::MAX`, serial `0` must be considered newer under sequence-space arithmetic.

## Relevant Source Code

The serial-number helper references [RFC 1982 Section 3.2](https://www.rfc-editor.org/rfc/rfc1982.html#section-3.2).

`crates/proto/src/rr/rdata/soa.rs:91-100`

```rust
/// The unsigned 32 bit version number of the original copy of the zone. Zone transfers
/// preserve this value. This value wraps and should be compared using sequence space arithmetic.
pub serial: u32,
```

`crates/proto/src/rr/serial_number.rs:45-65`

```rust
/// Serial Number Comparison, see RFC 1982, section 3.2
impl PartialOrd for SerialNumber {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        const SERIAL_BITS_HALF: u32 = 1 << (u32::BITS - 1);

        let i1 = self.0;
        let i2 = other.0;

        if i1 == i2 {
            Some(Ordering::Equal)
        } else if (i1 < i2 && (i2 - i1) < SERIAL_BITS_HALF)
            || (i1 > i2 && (i1 - i2) > SERIAL_BITS_HALF)
        {
            Some(Ordering::Less)
        } else if (i1 < i2 && (i2 - i1) > SERIAL_BITS_HALF)
            || (i1 > i2 && (i1 - i2) < SERIAL_BITS_HALF)
        {
            Some(Ordering::Greater)
        } else {
            None
        }
    }
}
```

`crates/proto/src/rr/rr_set.rs:291-304`

```rust
RecordType::SOA => {
    assert!(self.records.len() <= 1);

    if let Some(soa_record) = self.records.first() {
        match &soa_record.data {
            RData::SOA(existing_soa) => {
                if let RData::SOA(new_soa) = &record.data {
                    if new_soa.serial <= existing_soa.serial {
                        return false;
                    }
```

This SOA replacement check uses raw integer ordering instead of `SerialNumber`.

`crates/server/src/store/in_memory/inner.rs:556-568`

```rust
let records: &mut Arc<RecordSet> = self.records.entry(rr_key).or_insert_with(|| {
    Arc::new(RecordSet::new(
        record.name.clone(),
        record.record_type(),
        serial,
    ))
});

let mut records_clone = RecordSet::clone(&*records);
if records_clone.insert(record, serial) {
```

The server in-memory store reaches `RecordSet::insert` through `upsert`, so this is not dead code.

## Runtime Evidence

The recorded Rust probe ran with `cargo run --quiet`. It first checked the SerialNumber helper's comparison of `u32::MAX` and zero, then inserted an SOA with serial 4294967295 into a RecordSet and attempted to replace it with serial 0. The helper correctly treated the maximum serial as preceding zero in sequence space, but `RecordSet::insert` rejected the wrapped update. The stored serial remained 4294967295. This directly tests the helper and the record replacement API with the same wrap boundary.

Recorded inputs, output, and checks:

```text
serial_number_helper_max_lt_zero=true
first_inserted=true
wrapped_inserted=false
recordset_current_serial=4294967295
```

## Inconsistency Reason

[RFC 1035](https://www.rfc-editor.org/rfc/rfc1035.html) requires SOA serial comparison with sequence-space arithmetic. Hickory implements that arithmetic in `SerialNumber`, but the SOA replacement path uses `new_soa.serial <= existing_soa.serial`. Therefore a valid wrap from `4294967295` to `0` is treated as stale.

## Impact

After SOA serial wrap, a valid newer SOA can be ignored. That can leave an authoritative in-memory zone state stale until a later non-wrapped serial is accepted.

## Fix Direction

Compare SOA serials through `SerialNumber::new(new_soa.serial).partial_cmp(&SerialNumber::new(existing_soa.serial))`, and reject only `Less`, `Equal`, or the undefined half-space case according to the intended update policy.
