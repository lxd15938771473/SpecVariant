# Packet number spaces share a global send counter
## Merged Redundant Reports

The following report had the same implementation cause and the same required fix, so it has been merged into this report and the redundant file was removed:

- `1001-1500/0125-packet_number_allocator_shared.md`

The shared cause is that `quiche` stores separate packet number spaces but allocates outgoing packet numbers from one connection-wide `next_pkt_num`. The shared fix is to keep independent send packet-number counters for Initial, Handshake, and Application spaces while still sharing Application between 0-RTT and 1-RTT.

## Standard

- RFC 9000 Section 12.3: [Packet Numbers](https://www.rfc-editor.org/rfc/rfc9000.html#section-12.3) says Initial, Handshake, and Application are different packet number spaces, and each space starts at packet number `0`.
- RFC 9000 Section 19.3: [ACK Frames](https://www.rfc-editor.org/rfc/rfc9000.html#section-19.3) shows that different packet number spaces can reuse the same numeric packet number, so the send counters are intended to be independent.
- RFC 9001 Section 4.1.4: [Encryption Levels](https://www.rfc-editor.org/rfc/rfc9001.html#section-4.1.4) maps `0-RTT` and `1-RTT` to the Application packet number space. Sharing between those two is correct; sharing with Initial or Handshake is not.

```text
Packet numbers in each space start at packet number 0.
0-RTT and 1-RTT data exist in the same packet number space.
```

```text
Initial      -> Initial
0-RTT        -> Application data
Handshake    -> Handshake
Short Header -> Application data
```

## Code

- `quiche/src/packet.rs:152-160` maps `ZeroRTT` and `Short` to `Epoch::Application`, which matches the standard.
- `quiche/src/lib.rs:1323-1335` stores three packet number spaces but only one connection-wide `next_pkt_num`.
- `quiche/src/lib.rs:4389-4397` selects `pn` from `self.next_pkt_num` without regard to `epoch`.
- `quiche/src/lib.rs:5443-5466` increments that same `next_pkt_num` after every sent packet.

```rust
/// Packet number spaces.
pkt_num_spaces: [packet::PktNumSpace; packet::Epoch::count()],

/// Next packet number.
next_pkt_num: u64,

if pkt_num_manager.should_skip_pn(self.handshake_completed) {
    pkt_num_manager.set_skip_pn(Some(self.next_pkt_num));
    self.next_pkt_num += 1;
};
let pn = self.next_pkt_num;

// ...

self.next_pkt_num += 1;
```

So the bug is not the `0-RTT`/`1-RTT` mapping. The bug is that Initial, Handshake, and Application all consume one global send counter.

## Runtime Evidence

- Command: `cargo run --example pn_space_trace --quiet`

Fresh rerun, first transmitted packet in each space:

```text
client: Initial=0, Handshake=2, Short=3
server: Initial=0, Handshake=1, Short=3
```

Key log lines:

```text
[hex omitted] tx pkt Initial version=1 ... pn=0
[hex omitted] tx pkt Handshake version=1 ... pn=1
[hex omitted] tx pkt Short ... pn=3
```

Handshake and Application do not start at `0`, so the runtime behavior violates RFC 9000 Section 12.3.

## Conclusion

This is a real issue: quiche uses the correct packet-number-space mapping, but allocates send-side packet numbers from one global counter instead of one counter per space.
