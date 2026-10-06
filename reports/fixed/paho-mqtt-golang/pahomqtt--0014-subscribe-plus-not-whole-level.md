# SUBSCRIBE accepts partial + wildcards

## Problem Description

MQTT 3.1.1 requires the single-level wildcard `+` to occupy an entire Topic Filter level. Paho's subscribe validation does not inspect `+` placement inside each level, so filters such as `sport+` can be serialized.

## Summary

The runtime reproducer observed Paho sending a SUBSCRIBE packet with the invalid Topic Filter `sport+`.

## Standard Requirement

Official standard: [MQTT Version 3.1.1](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html)

MQTT 3.1.1 Section 4.7.1 says:

```text
Where it is used it MUST occupy an entire level of the filter.
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

No check rejects a level that contains `+` along with other characters.

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

The public subscribe path accepts the validator result and serializes the supplied filter.

## Implementation Behavior

Paho accepts Topic Filters where `+` is embedded in a level, such as `sport+` or `a+b`, instead of rejecting them before serialization.

## Runtime Evidence

What was done:
- Ran `go test -run ^TestSubscribeValidControl$ -count=1 -v` with a valid Topic Filter.
- Ran `go test -run ^TestSubscribePlusNotWholeLevelReproducer$ -count=1 -v` with `sport+`.

Observed result: Both tests exited with code `0`. The valid SUBSCRIBE control succeeded, and the reproducer serialized `sport+` into an outbound SUBSCRIBE packet instead of rejecting it.

## Inconsistency Reason

The standard requires `+` to be the only character in its topic level. Paho does not check that condition.

## Impact

Applications can send invalid subscriptions that strict brokers may reject or interpret inconsistently.

## Fix Direction

Reject any Topic Filter level containing `+` unless the level is exactly `+`. Add regression tests for valid `sport/+/ranking` and invalid `sport+` and `sport/tennis+`.
