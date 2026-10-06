# Stateless reset detection ignores NEW_CONNECTION_ID tokens

## Summary

`quiche` stores stateless reset tokens learned from `NEW_CONNECTION_ID`, but
`is_stateless_reset()` compares only
`peer_transport_params.stateless_reset_token`. After a client has actually sent
packets using a later server CID, a datagram ending in that later CID's token
is not recognized as a stateless reset.

## Standard Requirement

Official:
`https://www.rfc-editor.org/rfc/rfc9000.html#section-10.3`
`https://www.rfc-editor.org/rfc/rfc9000.html#section-10.3.1`

Section 10.3:

```text
A stateless reset token is specific to a connection ID. An endpoint
issues a stateless reset token by including the value in the Stateless
Reset Token field of a NEW_CONNECTION_ID frame. Servers can also issue
a stateless_reset_token transport parameter during the handshake that
applies to the connection ID that it selected during the handshake.
```

Section 10.3.1:

```text
An endpoint remembers all stateless reset tokens associated with the
connection IDs and remote addresses for datagrams it has recently sent.
This includes Stateless Reset Token field values from NEW_CONNECTION_ID
frames and the server's transport parameters but excludes stateless
reset tokens associated with connection IDs that are either unused or
retired.

The endpoint identifies a received datagram as a Stateless Reset by
comparing the last 16 bytes of the datagram with all stateless reset
tokens associated with the remote address on which the datagram was
received.
```

The required set is therefore: all used, unretired peer-CID tokens for the same
remote address, not just the handshake transport-parameter token.

## Relevant Source Code

`quiche/src/lib.rs:2921-2942`

```rust
fn is_stateless_reset(&self, buf: &[u8]) -> bool {
    if buf.len() < 21 {
        return false;
    }

    // TODO: we should iterate over all active destination connection IDs
    // and check against their reset token.
    match self.peer_transport_params.stateless_reset_token {
        Some(token) => crypto::verify_slices_are_equal(
            &token.to_be_bytes(),
            &buf[buf.len() - 16..buf.len()],
        )
        .is_ok(),
        None => false,
    }
}
```

`quiche/src/lib.rs:8695-8715`

```rust
frame::Frame::NewConnectionId {
    seq_num,
    retire_prior_to,
    conn_id,
    reset_token,
} => {
    let new_dcid_res = self.ids.new_dcid(
        conn_id.into(),
        seq_num,
        u128::from_be_bytes(reset_token),
        retire_prior_to,
        &mut retired_path_ids,
    );
```

`quiche/src/cid.rs:497-501`

```rust
let new_entry = ConnectionIdEntry {
    cid: cid.clone(),
    seq,
    reset_token: Some(reset_token),
    path_id: None,
};
```

## Implementation Behavior

The implementation does two different things:

1. It stores reset tokens for peer DCIDs received in `NEW_CONNECTION_ID`.
2. It never consults that DCID table when detecting stateless reset packets.

So the additional tokens exist in state, but the detector only checks the
handshake token.

## Inconsistency Reason

RFC 9000 requires comparison against all used, unretired stateless reset tokens
for the relevant remote address. `quiche` compares only one token, so any valid
stateless reset built from a later active CID token can be missed.

## Runtime Evidence

Run from the workspace root:

```powershell
$env:PATH='opt/tools/nasm-3.02/nasm-3.02;' + $env:PATH
$env:CARGO_TARGET_DIR='implementions/quiche/target'
cargo run --quiet --manifest-path 'opt/runs/rfc9000/rfc9000-quiche/501-1000/runtime/manual_verify_stateless_reset_scope/Cargo.toml'
```

Artifacts:

- Harness: `opt/runs/rfc9000/rfc9000-quiche/501-1000/runtime/manual_verify_stateless_reset_scope/src/main.rs`
- Output: `opt/runs/rfc9000/rfc9000-quiche/501-1000/runtime/manual_verify_stateless_reset_scope/result.txt`

Observed result:

```text
probe_uses_new_server_cid=true
closed_after_new_token=false
closed_after_initial_token=true
result=accepted_by_probe
```

This run proves three things:

1. The client really sent a packet using the new server CID learned from
   `NEW_CONNECTION_ID`.
2. A forged datagram ending in that new CID's token was not treated as a
   stateless reset.
3. A forged datagram ending in the initial handshake token did close the
   connection.

## Impact

After CID rotation or migration, a valid stateless reset for the currently used
peer CID can be ignored. That leaves the connection alive when RFC 9000 expects
reset handling.

## Fix Direction

- Iterate the used peer DCID set instead of only
  `peer_transport_params.stateless_reset_token`.
- Exclude unused and retired CIDs, as required by RFC 9000.
- Keep the comparison constant-time across the checked token set.
