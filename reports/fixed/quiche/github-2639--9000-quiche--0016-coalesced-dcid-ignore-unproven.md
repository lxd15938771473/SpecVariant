# Coalesced Different-DCID Packet Is Not Ignored

- Note: confirmed `SHOULD`-level compliance/hardening issue, not a `MUST`-level violation

## Standard

RFC 9000 Section 12.2: [Coalescing Packets](https://www.rfc-editor.org/rfc/rfc9000.html#section-12.2)

> Receivers MAY route based on the information in the first packet
> contained in a UDP datagram. ... Receivers SHOULD ignore any
> subsequent packets with a different Destination Connection ID than the
> first packet in the datagram.
>
> The receiver of coalesced QUIC packets MUST individually process each
> QUIC packet...

## Code

`implementions/quiche/quiche/src/lib.rs:2861-2880`

```rust
// Process coalesced packets.
while left > 0 {
    let read = match self.recv_single(
        &mut buf[len - left..len],
        &info,
        recv_pid,
    ) {
```

`implementions/quiche/quiche/src/lib.rs:3190-3201`

```rust
if !self.derived_initial_secrets {
    let (aead_open, aead_seal) = crypto::derive_initial_key_material(
        &hdr.dcid,
        self.version,
        self.is_server,
        false,
    )?;
```

`implementions/quiche/quiche/src/lib.rs:3335-3340`

```rust
let recv_pid = if hdr.ty == Type::Short && self.got_peer_conn_id {
    let pkt_dcid = ConnectionId::from_ref(&hdr.dcid);
    self.get_or_create_recv_path_id(recv_pid, &pkt_dcid, buf_len, info)?
} else {
    self.paths.get_active_path_id()?
};
```

These paths show that coalesced datagrams are processed packet-by-packet, and the long-header path has no datagram-level first-DCID ignore check.

## Runtime

Earlier `runtime/source_probe.py` output was only source inspection, so it is not used as runtime evidence here.

Command run from `implementions/quiche`:

```powershell
cargo test -p quiche coalesced_different_dcid_subsequent_handshake_processed -- --nocapture
```

The temporary test sent one datagram containing:

1. a valid `Initial` packet;
2. a valid `Handshake` packet whose DCID differed from the first packet.

Observed output:

```text
recv_before=1 recv_after=3 second_pn=2
recv_before=1 recv_after=3 second_pn=2
```

This shows that quiche processed both added packets and tracked the second `Handshake` packet, instead of ignoring it.

## Decision

In the tested valid `Initial + Handshake` case, quiche does not ignore a later coalesced packet with a different DCID. This report should therefore be kept in the write-up as a `SHOULD`-level issue.
