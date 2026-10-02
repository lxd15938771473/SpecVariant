# CID Exhaustion Still Allows Migration With Reused CID

## Summary

This merged report covers the requirement and the requirement. RFC 9000 says an endpoint that has exhausted available connection IDs cannot probe new paths, initiate migration, or respond to peer probes/migration attempts. s2n-quic can still create or activate a new path and fall back to the active path's `peer_connection_id` when no unused peer CID is available.

## Standard Requirement

Official standard: RFC 9000, Section 9.5, "Privacy Implications of Connection Migration"  
Link: https://www.rfc-editor.org/rfc/rfc9000#section-9.5

```text
An endpoint MUST NOT reuse a connection ID when sending from more
than one local address -- for example, when initiating connection
migration as described in Section 9.2 or when probing a new network
path as described in Section 9.1.

Similarly, an endpoint MUST NOT reuse a connection ID when sending to
more than one destination address.

An endpoint that exhausts available connection IDs cannot probe new
paths or initiate migration, nor can it respond to probes or attempts
by its peer to migrate.
```

The "cannot" sentence follows from the CID reuse prohibition: without an unused CID, the endpoint must not send on the new path using a CID already used on another path.

## Relevant Source Code

`quic/s2n-quic-transport/src/path/manager.rs:428-440`

```rust
let peer_connection_id = {
    if self.active_path().local_connection_id != datagram.destination_connection_id {
        // The peer changed destination CIDs, so we will attempt to switch to a new
        // destination CID as well. This could still just be a NAT rebind though, so
        // we continue with the existing destination CID if there isn't a new one
        // available.
        self.peer_id_registry
            .consume_new_id_for_new_path()
            .unwrap_or(self.active_path().peer_connection_id)
```

When a new destination path is created and no unused peer CID is available, the implementation reuses the active path's peer CID.

`quic/s2n-quic-transport/src/path/manager.rs:463-468`

```rust
let mut path = Path::new(
    *path_handle,
    peer_connection_id,
    datagram.destination_connection_id,
    rtt,
    cc,
```

The selected CID is stored on the new path.

`quic/s2n-quic-transport/src/path/manager.rs:682-687`

```rust
if !path_validation_probing.is_probing() && self.active_path_id() != path_id {
    amplification_outcome =
        self.update_active_path(path_id, random_generator, publisher)?;
```

A non-probing packet can activate that new path.

`quic/s2n-quic-transport/src/path/manager/tests.rs:1083-1090`

```rust
// The migration succeeds
assert!(res.is_ok());
assert_eq!(3, manager.paths.len());
// The new path uses the existing id since there wasn't a new one available
assert_eq!(
    manager.paths[res.unwrap().0.as_u8() as usize].peer_connection_id,
    id_2
);
```

The test explicitly asserts successful migration with the existing peer CID when no new CID is available.

`quic/s2n-quic-tests/src/tests/connection_migration.rs:27-37`

```rust
let on_socket = move |socket: io::Socket| {
    spawn(async move {
        let mut local_addr = socket.local_addr().unwrap();
        for _ in 0..rebind_count {
            local_addr = on_rebind(local_addr);
            delay(rebind_rate).await;
            socket.rebind(local_addr);
        }
    });
};
```

`quic/s2n-quic-tests/src/tests/connection_migration.rs:67-74`

```rust
stream.send(Bytes::from_static(b"A")).await.unwrap();
delay(rebind_rate / 2).await;

for _ in 0..rebind_count {
    stream.send(Bytes::from_static(b"B")).await.unwrap();
    delay(rebind_rate).await;
}
```

The client-side test shows local-address migration followed by continued sending.

## Implementation Behavior

s2n-quic has a path where connection migration succeeds even after no unused peer CID remains. For peer-address changes, it creates a new path and stores the active path's `peer_connection_id` on that path. For local-address changes, the client rebind test shows that s2n-quic continues sending after the local address changes.

## Inconsistency Reason

The standard requires different CIDs for sending on different paths and states that CID exhaustion prevents probing, initiating migration, and responding to peer migration/probes. s2n-quic instead falls back to an already used CID and still permits new-path behavior. That makes packets on different paths linkable and contradicts the RFC 9000 Section 9.5 privacy rule.

## Runtime Evidence

Command 1:

```text
cargo test -p s2n-quic-transport path::manager::tests::active_connection_migration_disabled -- --exact --nocapture
```

Result:

```text
test path::manager::tests::active_connection_migration_disabled ... ok
test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 353 filtered out
```

This covers `path/manager/tests.rs:1083-1090`, where migration succeeds and the new path uses the existing peer CID when no new CID is available.

Command 2:

```text
cargo test -p s2n-quic-tests tests::connection_migration::ip_rebind_test -- --exact --nocapture
```

Result:

```text
test tests::connection_migration::ip_rebind_test ... ok
test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 60 filtered out
```

Saved artifacts:

```text
runtime/0111-0114-final-cid-exhaust-active-migration.stdout.log
runtime/0111-0114-final-cid-exhaust-active-migration.stderr.log
runtime/0111-0114-final-cid-exhaust-active-migration.exitcode.txt
runtime/0111-0114-final-client-ip-rebind.stdout.log
runtime/0111-0114-final-client-ip-rebind.stderr.log
runtime/0111-0114-final-client-ip-rebind.exitcode.txt
runtime/0111-0114-final-summary.txt
```

## Impact

When available peer CIDs are exhausted, packets sent on different paths can still use the same Destination CID. Passive observers can correlate traffic across migration/probing paths, weakening the privacy protection intended by RFC 9000.

## Fix Direction

Do not create, probe, activate, or send on a new path when no unused peer CID is available for that path. Instead, wait for a new CID, request/retire CIDs through the existing CID management logic, or defer migration/probing until CID separation can be preserved.

## Merged Redundant Reports

The following reports described the same root cause and the same required fix, so they were merged into this report and removed:

- `0076-static unresolved requirement.md`: new path creation can reuse an existing peer CID when no unused CID is available.
- `0102-static unresolved requirement.md`: client local address rebinding can reuse the same Destination CID.
- `0103-static unresolved requirement.md`: duplicate of the local address rebinding Destination CID reuse issue.
- `0104-static unresolved requirement.md`: sending to a new destination address can reuse the existing Destination CID.
- `0112-0113-merged-static-unresolved-cid-exhaust-active.md`: active migration/probing under CID exhaustion has the same missing unused-peer-CID guard.
