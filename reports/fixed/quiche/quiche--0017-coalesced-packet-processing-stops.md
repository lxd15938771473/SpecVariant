# Coalesced datagram processing stops after one failed packet

## Summary

## Merged Redundant Reports

The following reports had the same implementation cause and the same required fix, so they have been merged into this report and the redundant files were removed:

- `1001-1500/0080-coalesced_after_ignored_long_header_not_processed.md`

The shared cause is that the coalesced receive loop stops or consumes the remaining datagram after one packet is ignored or reaches `Error::Done`. The shared fix is to discard only the offending packet, advance by that packet's consumed length, and continue parsing the remaining coalesced packets.

`quiche` stops processing a coalesced datagram when the current packet reaches
`Error::Done`. For some pre-authentication decryption failures, that causes a
later valid coalesced packet to be skipped. ## Standard Requirement

RFC 9000 Section 12.2: [Coalescing Packets](https://www.rfc-editor.org/rfc/rfc9000.html#section-12.2)

```text
Every QUIC packet that is coalesced into a single UDP datagram is
separate and complete.  The receiver of coalesced QUIC packets MUST
individually process each QUIC packet and separately acknowledge
them, as if they were received as the payload of different UDP
datagrams.  For example, if decryption fails (because the keys are
not available or for any other reason), the receiver MAY either
discard or buffer the packet for later processing and MUST attempt to
process the remaining packets.
```

This requirement applies to the reproducer used here: two coalesced `Initial`
packets with the same DCID. Section 12.2 only forbids following packets after
Retry, Version Negotiation, or short-header packets; it does not forbid this
shape.

## Relevant Source Code

`quiche/src/lib.rs:2861-2880`

```rust
while left > 0 {
    let read = match self.recv_single(&mut buf[len - left..len], &info, recv_pid) {
        Ok(v) => v,

        Err(Error::Done) => {
            if self.is_stateless_reset(&buf[len - left..len]) {
                self.mark_closed();
            }

            left
        },
```

If `recv_single()` returns `Error::Done`, `recv()` consumes all remaining bytes
in the datagram instead of moving to the next coalesced packet.

`quiche/src/lib.rs:3216-3245`, `quiche/src/lib.rs:3312-3321`,
`quiche/src/lib.rs:9338-9352`

```rust
let e = drop_pkt_on_err(Error::CryptoFail, self.recv_count, self.is_server, &self.trace_id);
return Err(e);
...
packet::decrypt_pkt(...).map_err(|e| {
    drop_pkt_on_err(e, self.recv_count, self.is_server, &self.trace_id)
})?;
...
if is_server && recv_count == 0 {
    return e;
}

Error::Done
```

After the server has already processed at least one packet, selected
pre-authentication key/decryption failures are downgraded to `Error::Done`.

## Implementation Behavior

The problematic path is:

1. the first coalesced packet reaches a pre-authentication failure;
2. `drop_pkt_on_err()` maps that failure to `Error::Done`;
3. the outer coalescing loop treats `Done` as `read = left`;
4. the later valid coalesced packet is never attempted.

## Runtime Evidence

Executed command:

```powershell
cargo test -p quiche manual_verify_coalesced_packet_processing_stops -- --ignored --nocapture
```

Observed output:

```text
first_packet_type=Initial
second_packet_type=Initial
same_dcid=true
first_packet_len=62
second_packet_len=62
control_ret=Ok(62)
control_recv_delta=1
repro_ret=Ok(124)
repro_recv_delta=0
```

Interpretation:

- the control case shows that the valid second `Initial` packet is processed
  normally (`control_recv_delta=1`);
- the reproducer sends `corrupt Initial || valid Initial` in one datagram;
- `repro_ret=Ok(124)` with `repro_recv_delta=0` means `quiche` consumed the
  whole datagram but did not process the later valid packet.

## Inconsistency Reason

RFC 9000 requires the receiver to attempt the remaining coalesced packets after
one packet fails decryption. `quiche` instead treats `Error::Done` as
"consume the rest of the datagram", which skips a later valid packet.

## Impact

A valid coalesced packet can be silently dropped behind an earlier
undecryptable packet, violating the receive behavior required by Section 12.2.

## Fix Direction

- Do not return `left` on `Err(Error::Done)` in the coalesced receive loop.
- Consume only the failed packet and continue with the remaining packet bytes.
- Keep the existing discard behavior for the failed packet itself.
