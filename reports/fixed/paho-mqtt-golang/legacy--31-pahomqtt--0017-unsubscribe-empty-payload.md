# UNSUBSCRIBE can be sent without a Topic Filter

## Problem Description

The public `Unsubscribe` API accepts an empty variadic topic list. Paho still allocates a Message Identifier and serializes an UNSUBSCRIBE packet with no Topic Filters in the payload.

## Summary

The issue is a missing empty-list check before packet construction and serialization.

## Standard Requirement

Official standard: [MQTT Version 3.1.1](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html)

MQTT 3.1.1 Section 3.10.3 requires at least one Topic Filter:

```text
The Payload of an UNSUBSCRIBE packet MUST contain at least one Topic Filter. An UNSUBSCRIBE packet with no payload is a protocol violation [MQTT-3.10.3-2].
```

## Relevant Source Code

`client.go:1197-1260`

```go
func (c *client) Unsubscribe(topics ...string) Token {
	...
	unsub := packets.NewControlPacket(packets.Unsubscribe).(*packets.UnsubscribePacket)
	unsub.Topics = make([]string, len(topics))
	copy(unsub.Topics, topics)
	...
}
```

There is no `len(topics) == 0` rejection.

`packets/unsubscribe.go:37-53`

```go
body.Write(encodeUint16(u.MessageID))
for _, topic := range u.Topics {
	body.Write(encodeString(topic))
}
```

With an empty topic list, the writer emits only the Message Identifier.

## Implementation Behavior

Paho can produce a syntactically framed UNSUBSCRIBE packet whose payload contains no Topic Filter.

## Runtime Evidence

What was done:
- Ran a valid UNSUBSCRIBE control containing one topic.
- Ran a reproducer calling UNSUBSCRIBE with no topics.

Observed result: The valid control serialized one topic. The reproducer serialized an UNSUBSCRIBE packet containing only the Message Identifier and no Topic Filters.

## Inconsistency Reason

MQTT requires at least one Topic Filter in every UNSUBSCRIBE payload. Paho does not check that the public API received any topics before writing the packet.

## Impact

The client can send a protocol-violating UNSUBSCRIBE packet that a broker should reject or treat as a protocol error.

## Fix Direction

Return an immediate token error when `Unsubscribe` is called with no topics, and add a defensive check in `UnsubscribePacket.Write`.
