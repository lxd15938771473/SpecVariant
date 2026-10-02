# Dropping Initial or Handshake state does not reset PTO backoff

## Summary
[RFC 9002 Section 6.2.2](https://www.rfc-editor.org/rfc/rfc9002.html#section-6.2.2) and [RFC 9002 Appendix A.11](https://www.rfc-editor.org/rfc/rfc9002.html#appendix-A.11) require resetting PTO state when Initial or Handshake keys are discarded, and Retry handling must also reset congestion-control and loss-recovery state for the restarted Initial exchange. quiche clears the discarded packet-number-space state and recomputes the timer, but leaves the global `pto_count` unchanged. After one PTO, discarding Initial state or accepting Retry can keep `pto_count = 1`, so later PTO calculation continues with exponential backoff instead of restarting from zero.

## Standard Requirement

Official standard: [RFC 9002 Section 6.2.2 "Handshakes and New Paths"](https://www.rfc-editor.org/rfc/rfc9002#section-6.2.2) and [Appendix A.11 "Upon Dropping Initial or Handshake Keys"](https://www.rfc-editor.org/rfc/rfc9002#appendix-A.11):

```text
When Initial or Handshake keys are discarded, the PTO and loss detection
timers MUST be reset, because discarding keys indicates forward
progress and the loss detection timer might have been set for a now-
discarded packet number space.

OnPacketNumberSpaceDiscarded(pn_space):
  assert(pn_space != ApplicationData)
  RemoveFromBytesInFlight(sent_packets[pn_space])
  sent_packets[pn_space].clear()
  // Reset the loss detection and PTO timer
  time_of_last_ack_eliciting_packet[pn_space] = 0
  loss_time[pn_space] = 0
  pto_count = 0
  SetLossDetectionTimer()
```

The reset includes both the timer target and the PTO backoff counter.

[RFC 9002 Section 6.3](https://www.rfc-editor.org/rfc/rfc9002.html#section-6.3) applies the same recovery reset requirement to Retry:

```text
Clients that receive a Retry packet reset congestion control and loss
recovery state, including resetting any pending timers.
```

## Relevant Source Code

`quiche/src/lib.rs:8847` drops keys and recovery state, then calls recovery discard for each path:

```rust
fn drop_epoch_state(&mut self, epoch: packet::Epoch, now: Instant) {
    let crypto_ctx = &mut self.crypto_ctx[epoch];
    if crypto_ctx.crypto_open.is_none() {
        return;
    }
    crypto_ctx.clear();
    self.pkt_num_spaces[epoch].clear();

    let handshake_status = self.handshake_status();
    for (_, p) in self.paths.iter_mut() {
        p.recovery
            .on_pkt_num_space_discarded(epoch, handshake_status, now);
    }
}
```

`quiche/src/lib.rs:3084` handles an accepted Retry by discarding Initial epoch state through the same recovery cleanup path:

```rust
if hdr.ty == Type::Retry {
    ...
    self.did_retry = true;
    self.set_initial_dcid(hdr.scid.clone(), None, self.paths.get_active_path_id()?)?;
    ...
    self.drop_epoch_state(packet::Epoch::Initial, now);
    self.got_peer_conn_id = false;
    self.handshake.clear()?;
    ...
    return Err(Error::Done);
}
```

Legacy recovery resets `pto_count` on ACK and increments it on PTO:

```rust
// quiche/src/recovery/congestion/recovery.rs:745
self.pto_count = 0;

// quiche/src/recovery/congestion/recovery.rs:797
self.pto_count += 1;
```

But legacy packet-number-space discard only clears per-epoch state and updates the timer:

```rust
// quiche/src/recovery/congestion/recovery.rs:843
fn on_pkt_num_space_discarded(
    &mut self, epoch: Epoch, handshake_status: HandshakeStatus, now: Instant,
) {
    let epoch = &mut self.epochs[epoch];

    let unacked_bytes = epoch
        .sent_packets
        .iter()
        .filter(|p| {
            p.in_flight && p.time_acked.is_none() && p.time_lost.is_none()
        })
        .fold(0, |acc, p| acc + p.size);

    self.bytes_in_flight.saturating_subtract(unacked_bytes, now);

    epoch.sent_packets.clear();
    epoch.clear_lost_frames();
    epoch.acked_frames.clear();

    epoch.time_of_last_ack_eliciting_packet = None;
    epoch.loss_time = None;
    epoch.loss_probes = 0;
    epoch.in_flight_count = 0;

    self.set_loss_detection_timer(handshake_status, now);
}
```

gcongestion has the same pattern:

```rust
// quiche/src/recovery/gcongestion/recovery.rs:883
self.pto_count = 0;

// quiche/src/recovery/gcongestion/recovery.rs:950
self.pto_count += 1;

// quiche/src/recovery/gcongestion/recovery.rs:1008
fn on_pkt_num_space_discarded(
    &mut self, epoch: packet::Epoch, handshake_status: HandshakeStatus,
    now: Instant,
) {
    let epoch = &mut self.epochs[epoch];
    self.bytes_in_flight
        .saturating_subtract(epoch.discard(&mut self.pacer), now);
    self.set_loss_detection_timer(handshake_status, now);
}
```

## Implementation Behavior

Discarding Initial or Handshake state removes sent packets, clears per-epoch loss state, and recomputes the loss detection timer. It does not reset the shared `pto_count`; only ACK processing does that. Retry processing also reaches Initial packet-number-space discard, so it inherits the same stale PTO backoff unless the recovery object is separately reset.

## Inconsistency Reason

The standard treats key discard as forward progress and requires PTO/loss-detection reset, with Appendix A.11 explicitly setting `pto_count = 0`. Retry likewise requires resetting congestion-control and loss-recovery state, including pending timers. quiche performs the timer/state cleanup but omits the PTO backoff reset, so a previous PTO expiration survives across packet-number-space discard and the restarted Retry exchange.

## Runtime Evidence

Focused test source:

```text
runtime-recheck-0014/discard_pto_count_reproducer.rs
```

Command, run from `implementions/quiche`:

```text
cargo test -p quiche codex_tmp_discard_pn_space_must_reset_pto_count --features gcongestion --release -- --nocapture
```

The test sends one Initial packet, fires one PTO so `pto_count = 1`, then discards the Initial packet-number space. Expected result: `pto_count` becomes `0`.

Observed result:

```text
reno: pto_count_after_pto=1 pto_count_after_discard=1
bbr2_gcongestion: pto_count_after_pto=1 pto_count_after_discard=1
cubic: pto_count_after_pto=1 pto_count_after_discard=1
test result: FAILED. 0 passed; 3 failed; 1093 filtered out
```

The command exited with code `101`, which is the expected reproducer signal for the failing assertions above.

A Retry-focused run exercises the same stale-backoff condition by sending an Initial packet, firing one PTO, accepting Retry, and then checking the post-Retry recovery state. Expected result: `pto_count` becomes `0`. Observed result:

```text
cubic: pto_count_after_pto=1 pto_count_after_retry=1
bbr2_gcongestion: pto_count_after_pto=1 pto_count_after_retry=1
test result: FAILED. 0 passed; 2 failed
```

## Impact

After Initial or Handshake state is discarded, PTO backoff can remain inflated. The same stale backoff can carry across Retry into the restarted Initial exchange. This can delay later probes and recovery even though the discard or Retry itself signals forward progress.

## Fix Direction

Set `pto_count = 0` in both `on_pkt_num_space_discarded()` implementations before `set_loss_detection_timer()`, matching Appendix A.11 while keeping the existing per-epoch cleanup. For accepted Retry, either rely on that corrected discard path or explicitly reinitialize the path recovery/congestion state so the restarted Initial exchange has no pre-Retry PTO backoff.
