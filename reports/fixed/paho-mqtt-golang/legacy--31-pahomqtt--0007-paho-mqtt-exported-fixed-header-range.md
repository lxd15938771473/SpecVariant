# Exported fixed-header MessageType lacks writer-side validation

## Problem Description

Normal Paho client paths construct packet types through constants, and inbound parsing derives the type from the four-bit wire field. The behavior occurs in direct packet construction: callers using the public `packets` subpackage can mutate an embedded `FixedHeader.MessageType`, and packet `Write` methods serialize that value without validating the MQTT packet-type range.

## Summary

This is a low-level packet-construction API finding. It is not evidence that the ordinary MQTT client path sends invalid packet types.

## Standard Requirement

Official standard: [MQTT Version 3.1.1](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html)

MQTT 3.1.1 Section 2.2.1 defines the Control Packet type as a four-bit value in byte 1, bits 7-4. Table 2.1 assigns packet types 1 through 14 and reserves values 0 and 15.

## Relevant Source Code

`packets/packets.go:57-72`

```go
const (
	Connect     = 1
	Connack     = 2
	Publish     = 3
	...
	Disconnect  = 14
)
```

`packets/packets.go:255-260`

```go
type FixedHeader struct {
	MessageType     byte
	Dup             bool
	Qos             byte
	Retain          bool
	RemainingLength int
}
```

`MessageType` is exported and can be changed by code that directly constructs packet structs.

`packets/packets.go:276-284`

```go
func (fh *FixedHeader) pack() (bytes.Buffer, error) {
	var header bytes.Buffer
	header.WriteByte(fh.MessageType<<4 | boolToByte(fh.Dup)<<3 | fh.Qos<<1 | boolToByte(fh.Retain))
	l, err := encodeLength(fh.RemainingLength)
	...
}
```

`packets/pingreq.go:25-40`

```go
type PingreqPacket struct {
	FixedHeader
}

func (pr *PingreqPacket) Write(w io.Writer) error {
	packet, err := pr.FixedHeader.pack()
	...
}
```

Concrete packet writers trust the embedded fixed header when serializing the first byte.

## Implementation Behavior

`NewControlPacket` and `ReadPacket` are constrained enough for the normal path. Direct packet struct construction is different: a caller can place reserved or wider-than-four-bit values in `FixedHeader.MessageType`, and `Write` emits a packet instead of returning an error.

## Runtime Evidence

What was done:
- Added and ran a focused Go reproducer with `go run .`.
- The reproducer checked a valid `NewControlPacket(Pingreq)` control, inbound parsing of reserved packet types, `NewControlPacketWithHeader(FixedHeader{MessageType:16})`, and direct `PingreqPacket` construction with mutated `MessageType` values.

Observed result: The run exited successfully. The valid PINGREQ serialized as `c000`; inbound `ReadPacket` rejected wire types `0x00` and `0xf0`; `NewControlPacketWithHeader` rejected `MessageType=16`; but directly constructed `PingreqPacket` values with `MessageType=0`, `15`, `16`, and `31` all returned `write_error="<nil>"`. The emitted bytes were `0000`, `f000`, `0000`, and `f000`, showing both reserved-type serialization and truncation of values above four bits.

## Inconsistency Reason

MQTT only permits defined control packet types 1 through 14 on the wire. Paho validates some construction paths, but its exported raw packet structs still allow invalid `MessageType` values to reach `FixedHeader.pack()`.

## Impact

Applications that use the public `packets` subpackage as a raw packet API can emit reserved MQTT packet types. The normal high-level client path appears protected, so the impact should be scoped to direct packet construction and testing tools built on this package.

## Fix Direction

Validate `FixedHeader.MessageType` in `pack()` or make each concrete `Write` method derive the packet type from the concrete packet struct instead of trusting caller-mutated header state.
