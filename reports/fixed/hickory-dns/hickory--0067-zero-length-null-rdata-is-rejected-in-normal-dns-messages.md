# Zero-length NULL RDATA is rejected in normal DNS messages

## Summary

Hickory supports non-empty arbitrary NULL RDATA, but rejects the RFC-allowed zero-length NULL RDATA in ordinary non-UPDATE DNS messages.

## Standard Requirement

Official standard: [RFC 1035 Section 3.3.10](https://www.rfc-editor.org/rfc/rfc1035.html#section-3.3.10), `NULL RDATA format (EXPERIMENTAL)`

```text
3.3.10. NULL RDATA format (EXPERIMENTAL)

    +--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+
    /                  <anything>                   /
    /                                               /
    +--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+

Anything at all may be in the RDATA field so long as it is 65535 octets
or less.
```

The standard sets only an upper bound of 65535 octets. It does not impose a content format or a non-zero minimum length, so `RDLENGTH=0` is within the allowed range.

## Relevant Source Code

`crates/proto/src/rr/rdata/null.rs:69-76`

```rust
impl<'r> RecordDataDecodable<'r> for NULL {
    fn read_data(decoder: &mut BinDecoder<'r>) -> Result<Self, DecodeError> {
        if decoder.is_empty() {
            Ok(Self::new())
        } else {
            let anything = decoder.read_vec_to_end().unverified(/*any byte array is good*/);
            Ok(Self::with(anything))
        }
    }
}
```

`NULL::read_data` itself accepts empty RDATA and preserves arbitrary non-empty bytes.

`crates/proto/src/rr/record.rs:344-348`

```rust
let rdata = if rd_length == 0 {
    RData::Update0(record_type)
} else {
    let decoder = decoder.split_off(rd_length as usize)?;
    RData::read(decoder, record_type)?
};
```

The generic record decoder maps every zero-length RDATA to `RData::Update0(record_type)` before `NULL::read_data` can run.

`crates/proto/src/op/message.rs:432-438`

```rust
let record = Record::read(decoder)?;
if op != OpCode::Update
    && record.record_type() != RecordType::OPT
    && record.data.is_update()
{
    return Err(DecodeError::InvalidEmptyRecord);
}
```

In non-UPDATE messages, that `Update0` marker is rejected as `InvalidEmptyRecord`.

## Implementation Behavior

For `TYPE=NULL` with non-empty RDATA, Hickory decodes the bytes through `RData::NULL` and can emit the same packet again. For `TYPE=NULL` with `RDLENGTH=0`, the record is classified as an UPDATE empty record and rejected in a normal DNS response.

## Inconsistency Reason

[RFC 1035](https://www.rfc-editor.org/rfc/rfc1035.html) allows NULL RDATA to contain any octet sequence up to 65535 octets, including the empty sequence. Hickory has a NULL-specific decoder that would accept that case, but the shared `Record::read` zero-length branch intercepts it and `Message::read_records` rejects it outside DNS UPDATE. Therefore the implementation is stricter than the RFC for zero-length NULL RDATA.

## Runtime Evidence

The recorded Rust probe ran with `cargo run --quiet` and constructed ordinary, non-UPDATE DNS response packets containing NULL answers. A NULL record with RDLENGTH=0 failed full message parsing with `InvalidEmptyRecord`. The control with payload `00 01 ff` parsed as a three-byte NULL record and survived encoding and decoding unchanged. A message containing a zero-length NULL followed by a non-empty NULL also failed. These results isolate rejection of the permitted empty NULL RDATA in ordinary message parsing.

Recorded inputs, output, and checks:

```text
case=zero_length_null
parse=err error=InvalidEmptyRecord

case=non_empty_null
parse=ok answers=1
answer1 record_type=NULL variant=NULL len=3 bytes=[00 01 ff] try_borrow_NULL=true
roundtrip_equal=true

case=mixed_zero_then_non_empty
parse=err error=InvalidEmptyRecord
```

## Impact

A valid DNS message containing zero-length NULL RDATA cannot be parsed by Hickory as a normal response. Non-empty arbitrary NULL RDATA is not affected.

## Fix Direction

Handle `RDLENGTH=0` according to the record type and opcode. For `RecordType::NULL` in non-UPDATE messages, route the empty RDATA to `RData::NULL(NULL::new())`; keep `Update0(record_type)` for DNS UPDATE semantics.
