# Paho accepts invalid fixed-header flags

## Problem Description

MQTT fixed-header flags are packet-type specific. Paho decodes the flag bits into `Dup`, `Qos`, and `Retain`, but it does not validate the decoded combination against MQTT 3.1.1 Table 2.2 before returning a concrete packet.

## Summary

The tested case accepts a SUBSCRIBE packet with invalid fixed-header flags instead of treating it as a protocol error.

## Standard Requirement

Official standard: [MQTT Version 3.1.1](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html)

MQTT 3.1.1 Section 2.2.2 requires reserved flag values to match Table 2.2:

```text
Where a flag bit is marked as "Reserved" in Table 2.2 - Flag Bits, it is reserved for future use and MUST be set to the value listed in that table [MQTT-2.2.2-1]. If invalid flags are received, the receiver MUST close the Network Connection [MQTT-2.2.2-2].
```

## Relevant Source Code

`packets/packets.go:287-295`

```go
func (fh *FixedHeader) unpack(typeAndFlags byte, r io.Reader) error {
	fh.MessageType = typeAndFlags >> 4
	fh.Dup = (typeAndFlags>>3)&0x01 > 0
	fh.Qos = (typeAndFlags >> 1) & 0x03
	fh.Retain = typeAndFlags&0x01 > 0
	...
}
```

The flag bits are decoded generically.

`packets/packets.go:211-242`

```go
func NewControlPacketWithHeader(fh FixedHeader) (ControlPacket, error) {
	switch fh.MessageType {
	case Subscribe:
		return &SubscribePacket{FixedHeader: fh}, nil
	...
	}
	return nil, fmt.Errorf("unsupported packet type 0x%x", fh.MessageType)
}
```

Dispatch validates the packet type, but not whether the flag nibble is legal for that packet type.

`net.go:123-146`

```go
if cp, err = packets.ReadPacketWithLimit(conn, maxIncomingPacketSize); err != nil {
	ibound <- inbound{err: err}
	close(ibound)
	return
}
```

The connection-close path depends on parser errors; accepted packets continue to higher-level handling.

## Implementation Behavior

Invalid flags can survive parsing because Paho separates the first byte into fields but does not compare the flag nibble with the allowed value for the decoded packet type.

## Runtime Evidence

What was done:
- Ran a valid fixed-header control through packet parsing.
- Ran a reproducer with invalid fixed-header flags.

Observed result: The valid control decoded successfully. The reproducer was also accepted as `*packets.SubscribePacket` with flags `0x00`, even though SUBSCRIBE requires the fixed-header flags `0x02`.

## Inconsistency Reason

MQTT requires receivers to close the connection on invalid fixed-header flags. Paho accepts at least one invalid packet-type/flag combination, so the parser does not enforce the required error path.

## Impact

Malformed packets can enter client state handling instead of immediately terminating the connection, which can hide peer protocol errors and lead to invalid follow-on state.

## Fix Direction

Add packet-type-specific fixed-header flag validation after unpacking the first byte and before returning the concrete packet.
