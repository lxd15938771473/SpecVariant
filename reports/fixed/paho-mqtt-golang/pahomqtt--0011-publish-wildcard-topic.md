# PUBLISH Topic Name accepts wildcards

## Problem Description

MQTT wildcard characters are valid in Topic Filters, but they must not appear in a PUBLISH Topic Name. Paho's public publish path copies the caller-supplied topic directly into `PublishPacket.TopicName` and serializes it without wildcard validation.

## Summary

The runtime reproducer observed a PUBLISH packet emitted with wildcard characters in the Topic Name.

## Standard Requirement

Official standard: [MQTT Version 3.1.1](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html)

MQTT 3.1.1 Sections 3.3.2 and 4.7.1 say:

```text
The Topic Name in the PUBLISH Packet MUST NOT contain wildcard characters.
```

```text
The wildcard characters can be used in Topic Filters, but MUST NOT be used within a Topic Name.
```

## Relevant Source Code

`client.go:813-828`

```go
pub := packets.NewControlPacket(packets.Publish).(*packets.PublishPacket)
pub.Qos = qos
pub.TopicName = topic
pub.Retain = retained
```

`Publish` stores the caller-supplied topic without wildcard validation.

`packets/publish.go:38-53`

```go
func (p *PublishPacket) Write(w io.Writer) error {
	...
	body.Write(encodeString(p.TopicName))
	...
}
```

`topic.go:39-54`

```go
// - A TopicName may not contain a wildcard.
// - A TopicFilter may only have a # (multi-level) wildcard as the last level.
// - A TopicFilter may contain any number of + (single-level) wildcards.
```

The repository documents the rule, but the public publish path does not enforce it.

## Implementation Behavior

Applications can publish to topics containing `#` or `+`. Paho serializes the invalid Topic Name instead of returning a client-side error.

## Runtime Evidence

What was done:
- Ran `go test -run ^TestPublishValidControl$ -count=1 -v` with a valid Topic Name.
- Ran `go test -run ^TestPublishWildcardTopicReproducer$ -count=1 -v` with wildcard characters in the Topic Name.

Observed result: Both tests exited with code `0`. The valid PUBLISH control succeeded, and the wildcard Topic Name was emitted in the reproducer.

## Inconsistency Reason

The standard forbids wildcard characters in PUBLISH Topic Names. Paho performs no wildcard check on the publish path.

## Impact

Strict brokers can reject the publish or close the connection. Broker-dependent behavior may also cause applications to believe an invalid publish request was accepted.

## Fix Direction

Validate PUBLISH Topic Names before serialization and reject `#` and `+`. Keep wildcard validation separate from Topic Filter validation because Topic Names and Topic Filters have different rules.
