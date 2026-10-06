# Inbound PUBLISH QoS 3 is delivered

## Problem Description

MQTT 3.1.1 forbids PUBLISH packets with both QoS bits set to 1 and requires a receiver to close the connection if such a packet is received. Paho parses QoS bits `11` as `Qos=3`, unpacks the PUBLISH, and forwards it to the application path.

## Summary

The runtime reproducer observed an inbound QoS 3 PUBLISH reaching the handler with `QoS=3`.

## Standard Requirement

Official standard: [MQTT Version 3.1.1](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html)

MQTT 3.1.1 Section 3.3.1 says:

```text
A PUBLISH Packet MUST NOT have both QoS bits set to 1.
If a Server or Client receives a PUBLISH Packet which has both QoS bits set to 1 it MUST close the Network Connection.
```

## Relevant Source Code

`packets/packets.go:287-295`

```go
func (fh *FixedHeader) unpack(typeAndFlags byte, r io.Reader) error {
	fh.MessageType = typeAndFlags >> 4
	fh.Qos = (typeAndFlags >> 1) & 0x03
	...
}
```

The fixed header decodes QoS bits but does not reject `3`.

`packets/packets.go:211-230`

```go
case Publish:
	return &PublishPacket{FixedHeader: fh}, nil
```

`packets/publish.go:60-83`

```go
if p.Qos > 0 {
	p.MessageID, err = decodeUint16(b)
	...
}
```

PUBLISH unpacking treats any QoS value greater than zero as carrying a Packet Identifier.

`net.go:241-243`

```go
case *packets.PublishPacket:
	output <- incomingComms{incomingPub: m}
```

The parsed packet is forwarded to the application-facing path.

## Implementation Behavior

Paho accepts the invalid QoS bit pattern as a PUBLISH with `Qos=3` and delivers it instead of closing the connection.

## Runtime Evidence

What was done:
- Ran `go test -run ^TestInboundPublishQos1AckControl$ -count=1 -v` with a valid QoS 1 inbound PUBLISH.
- Ran `go test -run ^TestInboundPublishQos3DeliveredReproducer$ -count=1 -v` with both QoS bits set.

Observed result: Both tests exited with code `0`. The QoS 1 control was acknowledged. The QoS 3 reproducer reached the handler, and the observed message carried `QoS=3`.

## Inconsistency Reason

The standard requires connection closure for PUBLISH QoS bits `11`. Paho parses and dispatches that packet as ordinary inbound data.

## Impact

Invalid PUBLISH packets can reach application code and be acknowledged, weakening protocol validation and potentially corrupting QoS state.

## Fix Direction

Reject PUBLISH fixed headers with QoS bits `11` during packet parsing and propagate the error so the connection closes. Add a regression test for byte 1 values with QoS `3`.
