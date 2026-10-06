# PUBLISH Topic Name length is truncated instead of rejected

## Problem Description

MQTT 3.1.1 limits Topic Names and Topic Filters to UTF-8 encodings of at most 65535 bytes. Paho accepts an overlong PUBLISH Topic Name and silently truncates the encoded string to 65535 bytes.

## Summary

The runtime reproducer observed serialization of an overlong Topic Name instead of rejection.

## Standard Requirement

Official standard: [MQTT Version 3.1.1](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html)

MQTT 3.1.1 Section 4.7.3 says:

```text
Topic Names and Topic Filters are UTF-8 encoded strings, they MUST NOT encode to more than 65535 bytes.
```

## Relevant Source Code

`client.go:813-839`

```go
pub := packets.NewControlPacket(packets.Publish).(*packets.PublishPacket)
pub.Qos = qos
pub.TopicName = topic
pub.Retain = retained
```

The public publish path does not reject overlong Topic Names.

`packets/publish.go:38-55`

```go
body.Write(encodeString(p.TopicName))
```

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

The encoder truncates overlong strings instead of returning an error.

## Implementation Behavior

An overlong Topic Name is accepted by `Publish`, shortened by `encodeBytes`, and emitted on the wire as a different topic value.

## Runtime Evidence

What was done:
- Ran `go test -run ^TestPublishValidControl$ -count=1 -v` with a valid Topic Name.
- Ran `go test -run ^TestPublishOverlongTopicReproducer$ -count=1 -v` with a Topic Name longer than 65535 bytes.

Observed result: Both tests exited with code `0`. The valid PUBLISH control succeeded, and the overlong Topic Name reproducer observed serialization rather than rejection; the emitted Topic Name was truncated to 65535 bytes on the wire.

## Inconsistency Reason

The standard forbids Topic Names over 65535 encoded bytes. Paho silently changes the topic by truncating it, which is not valid MQTT behavior.

## Impact

Applications can publish to a truncated topic different from the intended topic. Strict peers can reject the packet or treat the connection as malformed.

## Fix Direction

Return an error when an MQTT string field exceeds 65535 bytes instead of truncating it. Add a regression test that confirms overlong Topic Names fail before any bytes are written.
