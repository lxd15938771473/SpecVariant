# ECN validation is applied to the ACK-receive path instead of the packet-sending path

## Summary

`s2n-quic` has a real ECN path-attribution bug. `quic/s2n-quic-transport/src/recovery/manager.rs` aggregates ECN markings from all newly acknowledged packets, but `process_ecn()` validates only `context.path_mut()`. In a migration or multi-path ACK case, an ACK for packets sent on the old path can disable ECN on the new path.

## Standard Requirement

- Official standard: RFC 9000
- Section 13.4.2: ECN Validation
- Section 13.4.2.1: Receiving ACK Frames with ECN Counts
- Section 9.3 and 9.4: migration to a new path

```text
It is possible for faulty network devices to corrupt or erroneously
drop packets that carry a non-zero ECN codepoint. To ensure
connectivity in the presence of such devices, an endpoint validates
the ECN counts for each network path and disables the use of ECN on
that path if errors are detected.

The new path might not have the same ECN capability. Therefore, the
endpoint validates ECN capability as described in Section 13.4.

An endpoint that receives an ACK frame with ECN counts therefore
validates the counts before using them. It performs this validation
by comparing newly received counts against those from the last
successfully processed ACK frame. Any increase in the ECN counts is
validated based on the ECN markings that were applied to packets that
are newly acknowledged in the ACK frame.
```

RFC 9000 Section 13.4.2.1 says ACK_ECN counts are maintained per packet number space, not per path. That does not relax the path requirement above: validation failure still has to be attributed to the path that sent the newly acknowledged ECN-marked packets, not merely the path on which the ACK arrived.

## Relevant Source Code

`quic/s2n-quic-transport/src/path/mod.rs:140-149` shows that each `Path` has its own `ecn_controller`.

`quic/s2n-quic-transport/src/recovery/manager.rs:650-788` aggregates ECN markings across all newly acknowledged packets, then validates only the current path:

```rust
let mut newly_acked_ecn_counts = EcnCounts::default();

for (packet_number, acked_packet_info) in newly_acked_packets {
    let path = context.path_mut_by_id(acked_packet_info.path_id);
    let sent_bytes = acked_packet_info.sent_bytes as usize;
    newly_acked_ecn_counts.increment(acked_packet_info.ecn);
    // ...
}

if new_largest_packet {
    self.process_ecn(
        newly_acked_ecn_counts,
        ecn_counts,
        timestamp,
        context,
        publisher,
    );
}

fn process_ecn<Ctx: Context<Config>, Pub: event::ConnectionPublisher>(
    &mut self,
    newly_acked_ecn_counts: EcnCounts,
    ack_frame_ecn_counts: Option<EcnCounts>,
    timestamp: Timestamp,
    context: &mut Ctx,
    publisher: &mut Pub,
) {
    let path_id = context.path_id();
    let path = context.path_mut();

    let outcome = path.ecn_controller.validate(
        newly_acked_ecn_counts,
        self.sent_packet_ecn_counts,
        self.baseline_ecn_counts,
        ack_frame_ecn_counts,
        timestamp,
        path.rtt_estimator.smoothed_rtt(),
        path_event!(path, path_id),
        publisher,
    );

    self.baseline_ecn_counts = ack_frame_ecn_counts.unwrap_or_default();
}
```

`quic/s2n-quic-core/src/path/ecn.rs:231-283` applies the result to the controller passed in:

```rust
if ack_frame_ecn_counts.is_none() {
    if newly_acked_ecn_counts.as_option().is_some() {
        self.fail(now, path, publisher);
        return ValidationOutcome::Failed;
    }
}

if matches!(self.state, State::Unknown)
    && newly_acked_ecn_counts.ect_0_count > VarInt::from_u8(0)
{
    self.change_state(State::Capable(ce_suppression_timer), path, publisher);
}
```

If an ACK newly acknowledges ECN-marked packets but has no ACK_ECN counts, `validate()` fails the controller for the path object it was called on.

## Implementation Behavior

The recovery manager already uses `acked_packet_info.path_id` for congestion control and `on_packet_ack`, but not for ECN validation input. `newly_acked_ecn_counts`, `sent_packet_ecn_counts`, and `baseline_ecn_counts` are consumed as shared state, and the resulting state transition is always applied to `context.path_mut()`.

As a result, when an ACK received on path B newly acknowledges ECN-marked packets sent on path A, path B can be marked `Failed` even though the ECN evidence belongs to path A.

## Inconsistency Reason

The RFC allows ACK_ECN reporting to be scoped by packet number space, but it still requires validation and disablement per network path. This implementation mixes path A packet markings into one ECN aggregate and then applies failure to path B, the ACK-receive path. That is inconsistent with Section 13.4.2 (`for each network path` / `on that path`) and with the migration rule that the new path must validate its own ECN capability.

## Runtime Evidence

The previous runner output is not valid evidence for this issue. `runtime/check_candidate.py:121-145` falls back to `generic_suspected_signal()` for this requirement, which only returns `bool(text.strip())` for the target source file.

- Control command: `cargo test -p s2n-quic-transport recovery::manager::tests::process_new_acked_packets_process_ecn -- --exact --nocapture`
- Control result: passed

- Reproducer command: `cargo test -p s2n-quic-transport recovery::manager::tests::ecn_validation_failure_on_old_path_ack_disables_wrong_path -- --exact --nocapture`
- Reproducer result: passed

The reproducer first validates path 0 with ACK_ECN counts on path 0, then sends one more `ECT(0)` packet on path 0, and finally acknowledges that packet on path 1 without ACK_ECN counts. The test passes only if path 0 stays ECN-capable while path 1 falls back to `NotEct`. That is the wrong-path disablement described above.

## Impact

During migration or any multi-path ACK scenario, ECN failure detected from packets sent on the old path can disable ECN on the new path. This can cause false ECN fallback on the wrong path and leave the path that actually produced the ECN evidence in the wrong state.

## Fix Direction

Do not call `validate()` on `context.path_mut()` with cross-path ECN aggregates. ECN validation input needs to be attributed to the path that sent the newly acknowledged packets, and any failure must only change that path's ECN state. If the current shared `baseline_ecn_counts` and `sent_packet_ecn_counts` cannot support that attribution safely, they also need path-specific tracking.

## Merged Redundant Reports

The following report described the same root cause and the same required fix, so it was merged into this report and removed:

- `0002-the requirement.md`: ECN validation disables the ACK/current path instead of the path that sent the acknowledged ECN-marked packets.
