# missing administrator-facing spin-bit disable control

## Summary

- Caveat: this extracted requirement is narrower than RFC 9000. The RFC allows administrators to disable the spin bit **globally or per connection**; it does not require a per-connection-only control. The issue remains real because the current implementation provides neither.

## Standard

RFC 9000 Section 17.4: [Latency Spin Bit](https://www.rfc-editor.org/rfc/rfc9000.html#section-17.4)

```text
The spin bit is an OPTIONAL feature of this version of QUIC.  An
endpoint that does not support this feature MUST disable it, as
defined below.

Each endpoint unilaterally decides if the spin bit is enabled or
disabled for a connection.  Implementations MUST allow administrators
of clients and servers to disable the spin bit either globally or on
a per-connection basis.  Even when the spin bit is not disabled by
the administrator, endpoints MUST disable their use of the spin bit
for a random selection of at least one in every 16 network paths, or
for one in every 16 connection IDs
```

Interpretation: the RFC accepts either a global disable control or a per-connection disable control, but it still requires some administrator-facing control. A hard-coded always-disabled implementation is not enough.

## Code

`quic/s2n-quic-transport/src/space/application.rs:56-58`

```rust
    /// The current state of the Spin bit
    /// TODO: Spin me
    pub spin_bit: SpinBit,
```

`quic/s2n-quic-transport/src/space/application.rs:111-115`

```rust
        Self {
            tx_packet_numbers: TxPacketNumbers::new(PacketNumberSpace::ApplicationData, now),
            ack_manager,
            spin_bit: SpinBit::Zero,
```

`quic/s2n-quic-transport/src/space/application.rs:237-245`

```rust
        let spin_bit = self.spin_bit;
        let (_protected_packet, buffer) =
            self.key_set.encrypt_packet(buffer, |buffer, key, key_phase| {
                let packet = Short {
                    spin_bit,
                    key_phase,
```

`quic/s2n-quic-core/src/packet/short.rs:137-150`

```rust
        let spin_bit = SpinBit::from_tag(tag);
        let packet = Short {
            spin_bit,
            key_phase,
            destination_connection_id,
            packet_number,
            payload,
        };
```

`specs/todos/transport/17.4.toml:9-17`

```toml
[[TODO]]
quote = '''
Implementations MUST allow administrators
of clients and servers to disable the spin bit either globally or on
a per-connection basis.
'''
tracking-issue = "517"
```

What this shows:

- the transport stores a spin-bit field;
- the field is initialized to `SpinBit::Zero`;
- the transmit path copies that stored value into outgoing short headers;
- no update path or administrator-facing switch is present in the checked tree;
- the repo itself still tracks RFC 17.4 as unfinished.

## Runtime Evidence

- Command:

```text
cargo run --quiet --manifest-path tmp/spinbit-check/Cargo.toml
```

Observed output:

```text
total_packets=12
short_header_packets=9
short_header_spin_one_packets=0
```

This confirms that live 1-RTT short-header traffic keeps the spin bit cleared. The runtime result matches the static reading: the feature is hard-disabled, not administrator-configurable.

## Conclusion

The original report wording was too narrow because RFC 9000 does not require a per-connection-only disable control. The standards-accurate conclusion is: current `s2n-quic` provides no administrator-facing spin-bit disable control at all. Since RFC 9000 requires at least one of `global` or `per-connection`, the underlying issue is real.
