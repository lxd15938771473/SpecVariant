# Paho serializes overlong MQTT UTF-8 strings

## Problem Description

MQTT UTF-8 string components are limited to 65535 encoded bytes. Paho's generic string encoder does not reject longer fields; it truncates them and continues serializing the packet.

## Summary

The reachable issue is not a missing broker-side check. The client-side packet writer can turn an overlong caller-supplied string into a truncated wire field without reporting an error.

## Standard Requirement

Official standard: [MQTT Version 3.1.1](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html)

MQTT 3.1.1 Section 1.5.3 caps MQTT UTF-8 string components:

```text
Consequently there is a limit on the size of a string that can be passed in one of these UTF-8 encoded string components; you cannot use a string that would encode to more than 65535 bytes.
```

## Relevant Source Code

`packets/packets.go:347-355`

```go
func encodeBytes(field []byte) []byte {
	if len(field) > 65535 {
		field = field[0:65535]
	}
	fieldLength := make([]byte, 2)
	binary.BigEndian.PutUint16(fieldLength, uint16(len(field)))
	return append(fieldLength, field...)
}
```

The encoder truncates fields over 65535 bytes instead of returning an error.

`client.go:813-833`

```go
pub := packets.NewControlPacket(packets.Publish).(*packets.PublishPacket)
pub.Qos = qos
pub.TopicName = topic
pub.Retain = retained
```

The public `Publish` path copies the caller's Topic Name into the packet before the generic encoder writes it.

`packets/connect.go:143-167`

```go
if len(c.ClientIdentifier) > 65535 || len(c.Username) > 65535 || len(c.Password) > 65535 {
	return ErrProtocolViolation
}
```

CONNECT has a partial size check, but that does not protect Topic Names, Topic Filters, or other users of the shared encoder.

## Implementation Behavior

The writer produces a syntactically length-prefixed field even when the original API input was too long for MQTT. The caller sees no validation failure and the packet no longer represents the original string.

## Runtime Evidence

What was done:
- Ran a valid outbound string control through the packet writer.
- Ran an overlong-string reproducer through the same writer.

Observed result: The valid control wrote successfully. The reproducer also returned no write error and produced a packet containing a `0xffff` string length with total packet length 65551, showing truncation instead of rejection.

## Inconsistency Reason

The standard says an MQTT UTF-8 component larger than 65535 bytes cannot be used. Paho still uses it by silently shortening the field during serialization.

## Impact

Applications can believe they sent one Topic Name or other string value while the wire packet contains a truncated value, creating interoperability failures or topic misrouting.

## Fix Direction

Make MQTT string encoding return an error for fields above 65535 bytes and propagate that error through packet `Write` and public API token results. Keep the existing valid 65535-byte boundary case passing.
