# PUBLISH Topic Name accepts malformed UTF-8

## Problem Description

MQTT Topic Names are UTF-8 encoded strings and must not encode surrogate code points. Paho's publish path serializes the raw bytes of a Go string without checking UTF-8 validity, so malformed topic bytes can be sent.

## Summary

The runtime reproducer observed surrogate UTF-8 bytes emitted in a PUBLISH Topic Name.

## Standard Requirement

Official standard: [MQTT Version 3.1.1](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html)

MQTT 3.1.1 Sections 1.5.3 and 4.7.3 require Topic Names to be UTF-8 encoded strings and forbid surrogate-range encodings:

```text
In particular this data MUST NOT include encodings of code points between U+D800 and U+DFFF.
```

```text
Topic Names and Topic Filters are UTF-8 encoded strings.
```

## Relevant Source Code

`client.go:813-839`

```go
pub := packets.NewControlPacket(packets.Publish).(*packets.PublishPacket)
pub.Qos = qos
pub.TopicName = topic
pub.Retain = retained
```

`Publish` stores the caller-supplied topic directly.

`packets/publish.go:38-55`

```go
body.Write(encodeString(p.TopicName))
```

`packets/packets.go:323-355`

```go
func encodeString(field string) []byte {
	return encodeBytes([]byte(field))
}

func decodeString(b io.Reader) (string, error) {
	buf, err := decodeBytes(b)
	return string(buf), err
}
```

The shared helpers convert between strings and bytes without validating UTF-8 or rejecting surrogate encodings.

## Implementation Behavior

Paho can serialize malformed UTF-8 bytes in a PUBLISH Topic Name. The parser similarly converts decoded bytes to `string` without UTF-8 validation.

## Runtime Evidence

What was done:
- Ran `go test -run ^TestPublishValidControl$ -count=1 -v` with a valid UTF-8 Topic Name.
- Ran `go test -run ^TestPublishSurrogateUTF8TopicReproducer$ -count=1 -v` with surrogate UTF-8 bytes in the Topic Name.

Observed result: Both tests exited with code `0`. The valid PUBLISH control succeeded, and the reproducer emitted the malformed surrogate UTF-8 bytes in the Topic Name instead of rejecting the packet.

## Inconsistency Reason

The standard restricts Topic Names to valid MQTT UTF-8 strings. Paho performs raw byte serialization and does not enforce UTF-8 validity before sending.

## Impact

Strict brokers can reject the PUBLISH or close the connection. Malformed topic bytes can also cause inconsistent routing behavior across broker implementations.

## Fix Direction

Validate Topic Name UTF-8 before serialization and reject surrogate-range encodings. Add tests for a valid topic and a topic containing surrogate UTF-8 bytes.
