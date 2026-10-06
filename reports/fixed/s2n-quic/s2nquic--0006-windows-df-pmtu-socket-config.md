# Windows path misses DF / PMTU socket configuration

## Summary

Windows is a supported platform for `s2n-quic` (`README.md:121-123`), but the Windows build does not enable the `MtuDiscovery` feature. As a result, `configure_mtu_disc()` does not set IPv4 DF or PMTU discovery socket options, even though the local Windows UDP stack supports them. The Tokio path falls back to `mtu::Config::MIN`, limiting QUIC UDP payloads to 1200 bytes. That fallback partially matches RFC 9000 Section 14.2, but it does not satisfy the Section 14 requirement to set the IPv4 DF bit when possible.

## Standard Requirement

- RFC 9000 Section 14: [Datagram Size](https://www.rfc-editor.org/rfc/rfc9000.html#section-14)
- RFC 9000 Section 14.2: [Path Maximum Transmission Unit](https://www.rfc-editor.org/rfc/rfc9000.html#section-14.2)
- RFC 9000 Section 14.2.1: [Handling of ICMP Messages by PMTUD](https://www.rfc-editor.org/rfc/rfc9000.html#section-14.2.1)
- RFC 9000 Section 14.3: [Datagram Packetization Layer PMTU Discovery](https://www.rfc-editor.org/rfc/rfc9000.html#section-14.3)

The core requirement for this report is that IPv4 datagrams must set DF if possible. The PMTU fallback rule is separate: without PMTUD/DPLPMTUD, endpoints should avoid sending datagrams larger than the smallest allowed maximum datagram size.

## Relevant Source Code

### `README.md:121-123`

```text
`s2n-quic` can be built on Linux, MacOS, and Windows.
```

Windows is a supported platform, so this is not an out-of-scope platform issue.

### `quic/s2n-quic-platform/build.rs:85-115`

```rust
match env.target_os.as_str() {
    "linux" => {
        features.insert(MtuDiscovery);
        // ...
    }
    "android" => {
        features.insert(MtuDiscovery);
        // ...
    }
    _ => {
        // TODO others
    }
}
```

`MtuDiscovery` is enabled only on Linux and Android. Windows does not enable `s2n_quic_platform_mtu_disc`.

### `quic/s2n-quic-platform/src/syscall.rs:129-178`

```rust
pub fn configure_mtu_disc(tx_socket: &Socket) -> bool {
    let mut success = false;

    #[cfg(s2n_quic_platform_mtu_disc)]
    {
        success |= libc!(setsockopt(
            tx_socket.as_raw_fd(),
            libc::IPPROTO_IP,
            libc::IP_MTU_DISCOVER,
            &libc::IP_PMTUDISC_PROBE as *const _ as _,
            core::mem::size_of_val(&libc::IP_PMTUDISC_PROBE) as _,
        ))
        .is_ok();
    }

    success
}
```

Without `s2n_quic_platform_mtu_disc`, this function sets no DF / PMTU socket option and returns `false`.

### `quic/s2n-quic-platform/src/io/tokio.rs:127-136`

```rust
let mut mtu_config = mtu_config_builder.build()?;
if !syscall::configure_mtu_disc(&tx_socket) {
    mtu_config = mtu::Config::MIN;
}
```

The fallback is not an alternate Windows socket setting; it simply drops the MTU configuration to the minimum.

### `quic/s2n-quic-core/src/path/mtu.rs:135-180`

```rust
pub const MINIMUM_MAX_DATAGRAM_SIZE: u16 = 1200;
const MINIMUM_MTU: u16 = MINIMUM_MAX_DATAGRAM_SIZE
    + UDP_HEADER_LEN
    + const_min(IPV4_MIN_HEADER_LEN, IPV6_MIN_HEADER_LEN);
```

### `quic/s2n-quic-core/src/path/mtu.rs:347-352`

```rust
pub const MIN: Self = Self {
    initial_mtu: InitialMtu::MIN,
    base_mtu: BaseMtu::MIN,
    max_mtu: MaxMtu::MIN,
};
```

`Config::MIN` limits IPv4 QUIC UDP payloads to 1200 bytes.

### `quic/s2n-quic-transport/src/connection/transmission.rs:143-146`

```rust
let max_datagram_size = self
    .context
    .path()
    .clamp_datagram_size(buffer.len(), self.context.transmission_mode);
```

### `quic/s2n-quic-transport/src/path/mod.rs:508-519`

```rust
pub fn clamp_datagram_size(
    &self,
    requested_size: usize,
    transmission_mode: transmission::Mode,
) -> usize {
    requested_size.min(self.max_datagram_size(transmission_mode))
}
```

The send path clamps size, but does not configure IP-layer no-fragmentation behavior.

## Implementation Behavior

On Linux and Android, the code attempts to set `IP_MTU_DISCOVER` / `IPV6_MTU_DISCOVER = PROBE`, disabling kernel fragmentation and setting DF. On Windows, `MtuDiscovery` is not enabled, so `configure_mtu_disc()` has no socket-configuration effect. The Tokio path then falls back to `Config::MIN`, which reduces the send payload limit to 1200 bytes.

Therefore the implementation covers the fallback behavior, "without PMTUD, avoid sending above 1200," but it does not implement the mandatory "set IPv4 DF if possible" behavior on Windows.

## Inconsistency Reason

This is not a complete failure:

- Implemented part: if PMTU socket setup fails, QUIC datagrams are limited to a 1200-byte payload.
- Missing part: on Windows, a supported platform, the implementation does not use available `IP_DONTFRAGMENT` / `IP_MTU_DISCOVER` capabilities to satisfy RFC 9000 Section 14's `DF if possible` requirement.

The evidence supports a partial compliance gap rather than a fully rejected finding.

## Runtime Evidence

The existing `runtime/check_candidate.py` only confirmed that static signals remained present. I ran an actual Windows probe to check socket behavior and the s2n-quic fallback.

Command:

```text
cargo run --quiet --manifest-path tmp/audit_req_redacted_probe/Cargo.toml
```

Observed output:

```text
platform_os=windows
direct_configure_mtu_disc=false
default_ip_dontfragment=ok:0
default_ip_mtu_discover=ok:0
ip_dontfragment_result=ok:1
ip_mtu_discover_probe_result=ok:3
captured_is_min=true
captured_max_udp_payload_v4=1200
```

This shows:

- the default Windows UDP socket did not enable `IP_DONTFRAGMENT` or `IP_MTU_DISCOVER`;
- Windows could successfully set both options, so RFC 9000's `if possible` condition holds here;
- the current s2n-quic Windows path did not set them and instead fell back to `Config::MIN`.

## Impact

On Windows, the s2n-quic send path is limited to a 1200-byte UDP payload, which lowers the risk of oversized datagrams. However, it still does not explicitly enable the IPv4 DF / PMTU discovery configuration required by RFC 9000 Section 14 when possible.

## Fix Direction

1. Enable `MtuDiscovery` detection or the equivalent branch for Windows in `quic/s2n-quic-platform/build.rs`.
2. Set `IP_DONTFRAGMENT=1`, `IP_MTU_DISCOVER=IP_PMTUDISC_PROBE`, or an equivalent option on the Windows socket path.
3. Keep the current `mtu::Config::MIN` fallback for cases where setting the option fails or the platform truly does not support it.
