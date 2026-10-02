# PUBLISH accepts an empty Topic Name

## Problem Description

MQTT 3.1.1 requires all Topic Names and Topic Filters to be at least one character long. Paho's public `Publish` path assigns the caller-supplied topic directly to `PublishPacket.TopicName`, so an empty topic can be serialized as a zero-length MQTT string.

## Summary

The runtime reproducer observed an outbound PUBLISH packet with an empty Topic Name.

## Standard Requirement

Official standard: [MQTT Version 3.1.1](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html)

MQTT 3.1.1 Section 4.7.3 says:

```text
All Topic Names and Topic Filters MUST be at least one character long.
```

## Relevant Source Code

`client.go:813-839`

```go
func (c *client) Publish(topic string, qos byte, retained bool, payload interface{}) Token {
	...
	pub := packets.NewControlPacket(packets.Publish).(*packets.PublishPacket)
	pub.Qos = qos
	pub.TopicName = topic
	pub.Retain = retained
	...
}
```

`Publish` does not reject an empty topic before constructing the packet.

`packets/publish.go:38-55`

```go
func (p *PublishPacket) Write(w io.Writer) error {
	var body bytes.Buffer
	...
	body.Write(encodeString(p.TopicName))
	if p.Qos > 0 {
		body.Write(encodeUint16(p.MessageID))
	}
	...
}
```

The packet writer serializes `TopicName` directly.

`packets/packets.go:323-355`

```go
func encodeString(field string) []byte {
	return encodeBytes([]byte(field))
}
```

The shared encoder accepts an empty byte slice and emits length `0`.

## Implementation Behavior

An application can call `Publish("", ...)`; Paho builds a PUBLISH packet and writes a zero-length Topic Name instead of failing before send.

## Runtime Evidence

What was done:
- Ran `go test -run ^TestPublishValidControl$ -count=1 -v` with a non-empty topic.
- Ran `go test -run ^TestPublishEmptyTopicReproducer$ -count=1 -v` with an empty topic.

Observed result: Both tests exited with code `0`. The valid PUBLISH control succeeded, and the reproducer emitted a PUBLISH packet whose Topic Name length was zero.

## Inconsistency Reason

The standard forbids empty Topic Names. Paho does not validate the Topic Name length on the public publish path before encoding.

## Impact

Strict brokers can reject the packet or close the connection. Applications may see publish failures only after sending invalid wire data.

## Fix Direction

Reject empty topics in `Publish` before creating the packet, and add a regression test that expects an immediate token error for `Publish("", ...)`.
