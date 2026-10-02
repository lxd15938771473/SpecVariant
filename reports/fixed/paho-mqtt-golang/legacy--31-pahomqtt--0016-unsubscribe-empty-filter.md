# UNSUBSCRIBE accepts an empty Topic Filter

## Problem Description

MQTT 3.1.1 requires every Topic Filter to contain at least one character. Paho's public `Unsubscribe` path copies caller-supplied filters into the packet without the validation used by `Subscribe`, so an empty filter can be serialized.

## Summary

The runtime reproducer observed an outbound UNSUBSCRIBE packet containing an empty Topic Filter.

## Standard Requirement

Official standard: [MQTT Version 3.1.1](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html)

MQTT 3.1.1 Section 4.7.3 says:

```text
All Topic Names and Topic Filters MUST be at least one character long.
```

## Relevant Source Code

`client.go:1197-1221`

```go
func (c *client) Unsubscribe(topics ...string) Token {
	...
	unsub := packets.NewControlPacket(packets.Unsubscribe).(*packets.UnsubscribePacket)
	unsub.Topics = make([]string, len(topics))
	copy(unsub.Topics, topics)
```

`Unsubscribe` copies the filters without calling `validateTopicAndQos`.

`packets/unsubscribe.go:37-52`

```go
func (u *UnsubscribePacket) Write(w io.Writer) error {
	var body bytes.Buffer
	body.Write(encodeUint16(u.MessageID))
	for _, topic := range u.Topics {
		body.Write(encodeString(topic))
	}
	...
}
```

The packet writer serializes each supplied filter, including empty strings.

## Implementation Behavior

An application can call `Unsubscribe("")`; Paho writes a Topic Filter with length `0` into the UNSUBSCRIBE payload.

## Runtime Evidence

What was done:
- Ran `go test -run ^TestUnsubscribeValidControl$ -count=1 -v` with a non-empty Topic Filter.
- Ran `go test -run ^TestUnsubscribeEmptyFilterReproducer$ -count=1 -v` with an empty Topic Filter.

Observed result: Both tests exited with code `0`. The valid UNSUBSCRIBE control succeeded, and the reproducer emitted an UNSUBSCRIBE packet containing a zero-length Topic Filter.

## Inconsistency Reason

The standard forbids empty Topic Filters. Paho validates subscribe filters, but the unsubscribe path bypasses that validation.

## Impact

Strict brokers can reject the UNSUBSCRIBE packet or close the connection. Applications can also believe an unsubscribe request was sent successfully even though the packet is malformed.

## Fix Direction

Validate every filter in `Unsubscribe` before constructing the packet. Reuse the topic-filter validation logic and add tests for both valid and empty filters.
