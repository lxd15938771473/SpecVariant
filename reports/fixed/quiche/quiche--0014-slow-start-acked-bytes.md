# Reno slow start grows cwnd by MSS instead of acknowledged bytes

## Problem Description

In Reno slow start, quiche increases `congestion_window` by one `max_datagram_size` for each ACKed packet. [RFC 9002 Section 7.3.1](https://www.rfc-editor.org/rfc/rfc9002.html#section-7.3.1) requires the increase to equal the number of bytes acknowledged by the processed ACK, so non-MSS packets make cwnd grow too much.

## Standard Requirement

- RFC: <https://www.rfc-editor.org/rfc/rfc9002.html#section-7.3.1>

```text
While a sender is in slow start, the congestion window increases by
the number of bytes acknowledged when each acknowledgment is
processed.
```

This is byte-counted growth, not packet-counted growth.

## Relevant Source Code

`implementions/quiche/quiche/src/recovery/congestion/reno.rs:83-91`

```rust
if r.congestion_window < r.ssthresh.get() {
    // In Slow slart, bytes_acked_sl is used for counting
    // acknowledged bytes.
    r.bytes_acked_sl += packet.size;

    if r.hystart.in_css() {
        r.congestion_window += r.hystart.css_cwnd_inc(r.max_datagram_size);
    } else {
        r.congestion_window += r.max_datagram_size;
    }
```

`packet.size` is recorded, but the normal slow-start cwnd increment uses `r.max_datagram_size`.

`implementions/quiche/quiche/src/recovery/congestion/test_sender.rs:123-126`

```rust
acked.push(Acked {
    pkt_num: unacked.pkt_num,
    time_sent: unacked.time_sent,
    size: unacked.size,
```

The test helper passes the sent packet size into `Acked.size`, so a non-MSS packet can exercise the RFC byte-counted condition.

Existing Reno tests only ACK full-MSS packets:

`implementions/quiche/quiche/src/recovery/congestion/reno.rs:190-204`

```rust
let size = sender.max_datagram_size;
sender.ack_n_packets(1, size);
assert_eq!(sender.congestion_window, cwnd_prev + size);
```

That case cannot distinguish `packet.size` from `max_datagram_size`.

## Runtime Evidence

Temporary unit test added, run, then removed. The test sent a half-MSS packet and ACKed it while Reno was still in slow start.

Command:

```text
cargo test --release reno_slow_start_non_mss_acked_bytes_runtime_probe -- --nocapture
```

Observed output:

```text
running 1 test
mss=1200, acked_bytes=600, cwnd_before=24000, cwnd_after=25200, expected=24600

assertion `left == right` failed
  left: 25200
 right: 24600
test recovery::congestion::reno::tests::reno_slow_start_non_mss_acked_bytes_runtime_probe ... FAILED
```

The ACK acknowledged 600 bytes, but cwnd increased by 1200 bytes.

Project checks after removing the temporary test:

```text
python opt/scripts/run_all_self_tests.py
ok: true

python opt/scripts/run_total_test.py
ok: true
```

## Inconsistency Reason

[RFC 9002 Section 7.3.1](https://www.rfc-editor.org/rfc/rfc9002.html#section-7.3.1) requires slow-start growth by acknowledged bytes. quiche Reno instead grows by `max_datagram_size` per ACKed packet in the normal slow-start branch. This is equivalent only for full-MSS packets and over-increases cwnd for smaller ACKed packets.

## Impact

Reno slow start can become more aggressive than [RFC 9002](https://www.rfc-editor.org/rfc/rfc9002.html) permits when ACKed packets are smaller than `max_datagram_size`.

## Fix Direction

Use `packet.size` for the normal Reno slow-start increment, and add a regression test that ACKs a non-MSS packet in slow start.
