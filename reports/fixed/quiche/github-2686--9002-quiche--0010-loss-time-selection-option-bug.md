# Loss-time selection can discard an earlier deadline

## Summary
quiche's `loss_time_and_space()` directly compares `Option<Instant>` in both legacy recovery and gcongestion recovery. In Rust, `None < Some(deadline)` is `true`, so a later empty `loss_time` can overwrite an existing valid deadline. As a result, time-threshold loss detection can be skipped and the implementation can fall through to the PTO or timer-clear path.

## Standard Requirement

[RFC 9002 Section 6.2.1, Loss Detection Timer](https://www.rfc-editor.org/rfc/rfc9002.html#section-6.2.1):

```text
GetLossTimeAndSpace():
  time = loss_time[Initial]
  space = Initial
  for pn_space in [ Handshake, ApplicationData ]:
    if (time == 0 || loss_time[pn_space] < time):
      time = loss_time[pn_space];
      space = pn_space
  return time, space
```

The same section then requires:

```text
SetLossDetectionTimer():
  earliest_loss_time, _ = GetLossTimeAndSpace()
  if (earliest_loss_time != 0):
    // Time threshold loss detection.
    loss_detection_timer.update(earliest_loss_time)
    return
```

```text
OnLossDetectionTimeout():
  earliest_loss_time, pn_space = GetLossTimeAndSpace()
  if (earliest_loss_time != 0):
    // Time threshold loss Detection
    lost_packets = DetectAndRemoveLostPackets(pn_space)
```

The standard semantics are: when any non-zero `loss_time` exists, select the earliest deadline and its packet number space, then prioritize time-threshold loss detection.

## Relevant Source Code

`quiche/src/recovery/congestion/recovery.rs:440`

```rust
fn loss_time_and_space(&self) -> (Option<Instant>, Epoch) {
    let mut epoch = Epoch::Initial;
    let mut time = self.epochs[epoch].loss_time;

    for e in [Epoch::Handshake, Epoch::Application] {
        let new_time = self.epochs[e].loss_time;
        if time.is_none() || new_time < time {
            time = new_time;
            epoch = e;
        }
    }

    (time, epoch)
}
```

`quiche/src/recovery/gcongestion/recovery.rs:597`

```rust
fn loss_time_and_space(&self) -> (Option<Instant>, packet::Epoch) {
    let mut epoch = packet::Epoch::Initial;
    let mut time = self.epochs[epoch].loss_time;

    for e in [packet::Epoch::Handshake, packet::Epoch::Application] {
        let new_time = self.epochs[e].loss_time;
        if time.is_none() || new_time < time {
            time = new_time;
            epoch = e;
        }
    }

    (time, epoch)
}
```

The callers trust this helper:

`quiche/src/recovery/congestion/recovery.rs:507`

```rust
let (earliest_loss_time, _) = self.loss_time_and_space();

if let Some(to) = earliest_loss_time {
    self.loss_timer.update(to);
    return;
}
```

`quiche/src/recovery/congestion/recovery.rs:764`

```rust
let (earliest_loss_time, epoch) = self.loss_time_and_space();

if earliest_loss_time.is_some() {
    let (lost_packets, lost_bytes) =
        self.detect_lost_packets(epoch, now, trace_id);
```

## Implementation Behavior

When `Initial = Some(30)`, `Handshake = Some(10)`, and `Application = None`, the correct result is `Some(10), Handshake`. The current condition `new_time < time` treats `None` as smaller than `Some(10)`, so the final result is `None, Application`.

That means `set_loss_detection_timer()` cannot see the existing deadline, and `on_loss_detection_timeout()` does not enter the time-threshold loss-detection branch.

## Inconsistency Reason

The standard requires selecting the earliest non-zero loss deadline and using it to drive the loss detection timer. quiche treats "no deadline" (`None`) as a value that can defeat an existing deadline, so the returned value no longer represents the earliest loss time. The evidence supports the described behavior.

Note: the [RFC 9002 Section 6.2.1](https://www.rfc-editor.org/rfc/rfc9002.html#section-6.2.1) pseudocode is not defensive about `0` candidates, but in timer semantics `0` means there is no loss time and must not overwrite an existing non-zero deadline.

## Runtime Evidence

A minimal Rust probe was run:

```text
rustc --edition=2021 runtime/loss_time_option_probe.rs -o runtime/loss_time_option_probe.exe
runtime/loss_time_option_probe.exe
```

The probe compared the relevant `Option<Instant>` ordering case and observed:

```text
case=[Some(30), Some(10), None]
actual=(None, Application)
expected=(Some(10), Handshake)
none_lt_some=true
```

The auxiliary source probe was also re-run and reported `loss_time_option_selection_bug` as `ok: true`, confirming that the source still contains the same direct `Option<Instant>` comparison in both recovery implementations.

## Impact

If an earlier packet number space has a time-threshold loss deadline while a later space has no deadline, quiche can clear or skip that loss deadline and delay or miss loss detection that should run immediately.

## Fix Direction

Ignore `None` candidates during comparison. Allow only `Some(candidate)` to replace the current value when the current value is empty or the candidate is earlier:

```rust
if let Some(new_time) = new_time {
    if time.is_none() || Some(new_time) < time {
        time = Some(new_time);
        epoch = e;
    }
}
```
