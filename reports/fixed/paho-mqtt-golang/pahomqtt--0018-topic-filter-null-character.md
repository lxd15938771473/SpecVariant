# Topic Filter accepts U+0000

## Problem Description

MQTT 3.1.1 forbids U+0000 in Topic Filters. Paho's subscribe validation does not check for U+0000, and the unsubscribe path copies filters without calling the validator.

## Summary

The runtime reproducer observed a SUBSCRIBE packet whose Topic Filter contains U+0000.

## Standard Requirement

Official standard: [MQTT Version 3.1.1](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html)

MQTT 3.1.1 Section 4.7.3 says:

```text
Topic Names and Topic Filters MUST NOT include the null character (Unicode U+0000).
```

## Relevant Source Code

`topic.go:74-89`

```go
func validateTopicAndQos(topic string, qos byte) error {
	if len(topic) == 0 {
		return ErrInvalidTopicEmptyString
	}

	levels := strings.Split(topic, "/")
	for i, level := range levels {
		if level == "#" && i != len(levels)-1 {
			return ErrInvalidTopicMultilevel
		}
	}
	...
	return nil
}
```

The shared subscribe validator checks empty strings, `#` placement, and QoS, but not U+0000.

`packets/subscribe.go:38-56`

```go
for i, topic := range s.Topics {
	body.Write(encodeString(topic))
	body.WriteByte(s.Qoss[i])
}
```

`packets/unsubscribe.go:37-52`

```go
for _, topic := range u.Topics {
	body.Write(encodeString(topic))
}
```

Both packet writers serialize filters directly.

## Implementation Behavior

Paho can send Topic Filters containing the null character. The subscribe path accepts the filter because validation omits this check, and the unsubscribe path lacks filter validation.

## Runtime Evidence

What was done:
- Ran `go test -run ^TestSubscribeValidControl$ -count=1 -v` with a valid Topic Filter.
- Ran `go test -run ^TestSubscribeNullFilterReproducer$ -count=1 -v` with a Topic Filter containing U+0000.

Observed result: Both tests exited with code `0`. The valid SUBSCRIBE control succeeded, and the reproducer emitted a SUBSCRIBE packet containing the NUL byte in the Topic Filter.

## Inconsistency Reason

The standard forbids U+0000 in Topic Filters. Paho's filter validation and encoding paths do not enforce that rule.

## Impact

Strict brokers can reject the subscription or close the connection. Applications may see inconsistent subscription behavior across MQTT brokers.

## Fix Direction

Reject U+0000 in every Topic Filter path, including `Subscribe`, `SubscribeMultiple`, and `Unsubscribe`. Add regression tests that confirm valid filters pass and NUL-containing filters fail before serialization.
