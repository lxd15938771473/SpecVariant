# SUBSCRIBE accepts malformed UTF-8 Topic Filters

## Problem Description

The public `Subscribe` path can serialize a Topic Filter containing malformed UTF-8. The path validates empty topics, wildcard placement, and QoS, but not MQTT UTF-8 well-formedness.

## Summary

This is the SUBSCRIBE-specific form of the shared string-validation issue.

## Standard Requirement

Official standard: [MQTT Version 3.1.1](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html)

MQTT 3.1.1 Section 3.8.3 requires SUBSCRIBE Topic Filters to be MQTT UTF-8 strings:

```text
The Topic Filters in a SUBSCRIBE packet payload MUST be UTF-8 encoded strings as defined in Section 1.5.3 [MQTT-3.8.3-1].
```

## Relevant Source Code

`client.go:884-957`

```go
if err := validateTopicAndQos(topic, qos); err != nil {
	token.setError(err)
	return token
}
sub.Topics = append(sub.Topics, topic)
```

The caller-supplied filter is accepted after topic/QoS validation.

`topic.go:74-89`

```go
if len(topic) == 0 {
	return ErrInvalidTopicEmptyString
}
...
if qos > 2 {
	return ErrInvalidQos
}
```

The helper does not validate UTF-8.

`packets/subscribe.go:38-56`

```go
for i, topic := range s.Topics {
	body.Write(encodeString(topic))
	body.WriteByte(s.Qoss[i])
}
```

The packet writer serializes each Topic Filter through the shared raw string encoder.

## Implementation Behavior

Malformed UTF-8 can pass through subscription validation and be emitted in the SUBSCRIBE payload.

## Runtime Evidence

What was done:
- Ran a valid SUBSCRIBE Topic Filter control.
- Ran a reproducer with malformed UTF-8 in the Topic Filter.

Observed result: The valid control serialized cleanly. The reproducer also serialized and emitted the malformed UTF-8 bytes in the Topic Filter.

## Inconsistency Reason

MQTT requires SUBSCRIBE Topic Filters to be valid MQTT UTF-8 strings. Paho validates only other topic properties before writing the field.

## Impact

The client can send malformed subscription filters that brokers may reject or interpret inconsistently.

## Fix Direction

Add MQTT UTF-8 validation to `validateTopicAndQos` or directly before `SubscribePacket.Write` serializes Topic Filters.
