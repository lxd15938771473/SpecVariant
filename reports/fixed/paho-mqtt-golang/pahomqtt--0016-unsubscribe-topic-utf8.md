# UNSUBSCRIBE accepts malformed UTF-8 Topic Filters

## Problem Description

The public `Unsubscribe` path copies caller-supplied topics directly into an UNSUBSCRIBE packet. The writer then encodes each Topic Filter without validating MQTT UTF-8.

## Summary

Unlike SUBSCRIBE, this path does not call `validateTopicAndQos`; malformed strings can reach the packet writer directly.

## Standard Requirement

Official standard: [MQTT Version 3.1.1](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html)

MQTT 3.1.1 Section 3.10.3 requires UNSUBSCRIBE Topic Filters to be MQTT UTF-8 strings:

```text
The Topic Filters in an UNSUBSCRIBE packet MUST be UTF-8 encoded strings as defined in Section 1.5.3, packed contiguously [MQTT-3.10.3-1].
```

## Relevant Source Code

`client.go:1197-1260`

```go
unsub := packets.NewControlPacket(packets.Unsubscribe).(*packets.UnsubscribePacket)
unsub.Topics = make([]string, len(topics))
copy(unsub.Topics, topics)
```

The public path copies the variadic topics directly.

`packets/unsubscribe.go:37-53`

```go
for _, topic := range u.Topics {
	body.Write(encodeString(topic))
}
```

Each Topic Filter is serialized with the shared raw string encoder.

## Implementation Behavior

Paho emits whatever byte sequence is stored in the Go string. It does not reject malformed UTF-8 before writing UNSUBSCRIBE.

## Runtime Evidence

What was done:
- Ran a valid UNSUBSCRIBE Topic Filter control.
- Ran a reproducer with malformed UTF-8 in the Topic Filter.

Observed result: The valid control serialized cleanly. The reproducer also serialized and emitted malformed UTF-8 bytes in the Topic Filter.

## Inconsistency Reason

MQTT requires UNSUBSCRIBE Topic Filters to be valid MQTT UTF-8 strings. Paho writes the field without performing that validation.

## Impact

The client can send malformed unsubscribe requests that violate MQTT and may be rejected by strict brokers.

## Fix Direction

Validate all UNSUBSCRIBE Topic Filters with the same MQTT UTF-8 rules used for SUBSCRIBE and PUBLISH topics before writing the packet.
