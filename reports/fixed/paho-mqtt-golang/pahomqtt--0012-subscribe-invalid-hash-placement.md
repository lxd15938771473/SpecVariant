# SUBSCRIBE accepts embedded # wildcards

## Problem Description

MQTT 3.1.1 allows the multi-level wildcard `#` only as its own final topic-filter level, either alone or immediately after a topic separator. Paho's subscribe validation only rejects a level that is exactly `#` when that level is not last, so embedded forms such as `sport/tennis#` pass validation.

## Summary

The runtime reproducer observed Paho serializing a SUBSCRIBE packet with the invalid Topic Filter `sport/tennis#`.

## Standard Requirement

Official standard: [MQTT Version 3.1.1](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html)

MQTT 3.1.1 Section 4.7.1 says:

```text
The multi-level wildcard character MUST be specified either on its own or following a topic level separator.
```

## Relevant Source Code

`topic.go:68-87`

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

	if qos > 2 {
		return ErrInvalidQos
	}
```

The validator only catches a whole-level `#` before the final level. It does not catch `#` embedded in a larger level.

`client.go:903-918`

```go
sub := packets.NewControlPacket(packets.Subscribe).(*packets.SubscribePacket)
if err := validateTopicAndQos(topic, qos); err != nil {
	token.setError(err)
	return token
}
sub.Topics = append(sub.Topics, topic)
sub.Qoss = append(sub.Qoss, qos)
```

The public subscribe path relies on that validator before writing the filter into the SUBSCRIBE packet.

## Implementation Behavior

Filters such as `sport/tennis#` and `sport/#suffix` can pass validation even though `#` is not a complete final wildcard level.

## Runtime Evidence

What was done:
- Ran `go test -run ^TestSubscribeValidControl$ -count=1 -v` with a valid Topic Filter.
- Ran `go test -run ^TestSubscribeInvalidHashPlacementReproducer$ -count=1 -v` with `sport/tennis#`.

Observed result: Both tests exited with code `0`. The valid SUBSCRIBE control succeeded, and the reproducer serialized `sport/tennis#` into an outbound SUBSCRIBE packet instead of rejecting it.

## Inconsistency Reason

The standard requires `#` to stand alone as the final wildcard level. Paho only checks one invalid placement form and misses embedded `#` inside a level.

## Impact

Applications can send invalid subscriptions that strict brokers may reject, causing subscription setup failures or inconsistent behavior across brokers.

## Fix Direction

Validate every Topic Filter level: reject any level containing `#` unless the level is exactly `#` and it is the final level. Add positive tests for `#` and `sport/#`, and negative tests for `sport/tennis#` and `sport/#suffix`.
