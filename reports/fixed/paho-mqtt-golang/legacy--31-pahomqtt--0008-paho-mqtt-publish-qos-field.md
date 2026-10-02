# Public Publish QoS can corrupt fixed-header bits

## Problem Description

The public `Publish` API accepts a raw `byte` for QoS. Values above 2 are copied into the packet and later shifted into the fixed header without masking, so high bits can alter the MQTT Control Packet type nibble.

## Summary

The normal PUBLISH QoS field is only two bits wide. Paho validates subscription QoS elsewhere, but the `Publish` path does not apply the same range check.

## Standard Requirement

Official standard: [MQTT Version 3.1.1](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html)

MQTT 3.1.1 Section 2.2.2 defines the fixed-header flag nibble and Section 3.3.1 defines the PUBLISH QoS bits:

```text
The remaining bits [3-0] of byte 1 in the fixed header contain flags specific to each MQTT Control Packet type as listed in the Table 2.2 - Flag Bits below.
```

```text
PUBLISH ... DUP QoS QoS RETAIN
```

The QoS value belongs in bits 2 and 1; it must not spill into the packet-type bits.

## Relevant Source Code

`client.go:813-839`

```go
pub := packets.NewControlPacket(packets.Publish).(*packets.PublishPacket)
pub.Qos = qos
pub.TopicName = topic
pub.Retain = retained
```

The public API copies caller-controlled QoS directly.

`packets/packets.go:276-284`

```go
header.WriteByte(fh.MessageType<<4 |
	boolToByte(fh.Dup)<<3 |
	fh.Qos<<1 |
	boolToByte(fh.Retain))
```

`fh.Qos` is shifted without `& 0x03`.

`topic.go:74-89`

```go
if qos > 2 {
	return ErrInvalidQos
}
```

The project already has a QoS range check for subscription validation, but it is not used by `Publish`.

## Implementation Behavior

For normal QoS values 0, 1, and 2, Paho encodes the expected PUBLISH header. For a wider byte, the shifted value can overwrite the high nibble and change the packet type that appears on the wire.

## Runtime Evidence

What was done:
- Ran a valid PUBLISH QoS control.
- Ran a reproducer using a QoS byte with high bits set.

Observed result: The valid control produced first byte `0x32`, meaning packet type 3 with flag nibble `0x2`. The reproducer produced first byte `0x70`, meaning the packet type nibble changed to 7 and the flag nibble became `0x0`.

## Inconsistency Reason

MQTT defines PUBLISH QoS as a two-bit field. Paho copies and shifts an unrestricted byte, so invalid API input can escape the field boundary and corrupt the fixed header.

## Impact

A caller mistake can produce a different MQTT control packet type on the wire, causing interoperability failure and potentially confusing downstream protocol state.

## Fix Direction

Reject `Publish` QoS values above 2 before constructing the packet, and mask fixed-header QoS defensively in the low-level writer.
