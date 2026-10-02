# PUBLISH Topic Name accepts U+0000

## Problem Description

MQTT 3.1.1 forbids U+0000 in Topic Names and Topic Filters. Paho's publish path assigns and serializes the caller-supplied Topic Name without checking for the null character.

## Summary

The runtime reproducer observed a PUBLISH packet whose Topic Name contains U+0000.

## Standard Requirement

Official standard: [MQTT Version 3.1.1](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html)

MQTT 3.1.1 Sections 1.5.3 and 4.7.3 say:

```text
A UTF-8 encoded string MUST NOT include an encoding of the null character U+0000.
```

```text
Topic Names and Topic Filters MUST NOT include the null character (Unicode U+0000).
```

## Relevant Source Code

`client.go:813-839`

```go
pub := packets.NewControlPacket(packets.Publish).(*packets.PublishPacket)
pub.Qos = qos
pub.TopicName = topic
pub.Retain = retained
```

The public publish path stores the topic without validation.

`packets/publish.go:38-55`

```go
func (p *PublishPacket) Write(w io.Writer) error {
	var body bytes.Buffer
	...
	body.Write(encodeString(p.TopicName))
	...
	packet.Write(body.Bytes())
	packet.Write(p.Payload)
```

`packets/packets.go:323-355`

```go
func encodeString(field string) []byte {
	return encodeBytes([]byte(field))
}
```

Encoding converts the topic to bytes directly and does not reject U+0000.

## Implementation Behavior

Paho can serialize `TopicName` values containing the null character. The same raw string conversion pattern is also used on decode paths.

## Runtime Evidence

What was done:
- Ran `go test -run ^TestPublishValidControl$ -count=1 -v` with a normal non-empty Topic Name.
- Ran `go test -run ^TestPublishNullTopicReproducer$ -count=1 -v` with a Topic Name containing U+0000.

Observed result: Both tests exited with code `0`. The valid PUBLISH control succeeded, and the reproducer emitted a PUBLISH packet containing the NUL byte in the Topic Name instead of rejecting it.

## Inconsistency Reason

The standard forbids U+0000 in Topic Names. Paho's writer path treats the topic as raw bytes and performs no null-character check.

## Impact

Strict brokers can reject the packet or close the connection. Applications can accidentally publish malformed topic names and see broker-dependent behavior.

## Fix Direction

Validate PUBLISH Topic Names before serialization and reject U+0000. Add regression tests for a valid topic and a topic containing `\x00`.
