# Stored PUBLISH replay order is not preserved

## Problem Description

MQTT 3.1.1 requires resent QoS 1 and QoS 2 PUBLISH packets to be resent in the same order as the original PUBLISH packets. Paho resumes stored outbound PUBLISH packets in `Store.All()` order, and the default `MemoryStore` returns Go map iteration order rather than send order.

## Summary

The runtime reproducer observed ordered replay from an ordered store, but out-of-order replay from the default `MemoryStore`.

## Standard Requirement

Official standard: [MQTT Version 3.1.1](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html)

MQTT 3.1.1 Section 4.6 requires resend ordering:

```text
When it re-sends any PUBLISH packets, it MUST re-send them in the order in which the original PUBLISH packets were sent (this applies to QoS 1 and QoS 2 messages).
```

## Relevant Source Code

`client.go:1097-1106`

```go
storedKeys := c.persist.All()
for _, key := range storedKeys {
	packet := c.persist.Get(key)
	...
	if isKeyOutbound(key) {
```

Resume order depends on the store's key order.

`client.go:1145-1167`

```go
case *packets.PublishPacket:
	if p.Qos != 0 {
		p.Dup = true
	}
	token := newToken(packets.Publish).(*PublishToken)
	token.messageID = details.MessageID
	c.claimID(token, details.MessageID)
	c.obound <- &PacketAndToken{p: p, t: token}
```

Stored PUBLISH packets are resent in that iteration order.

`memstore.go:102-116`

```go
func (store *MemoryStore) All() []string {
	...
	var keys []string
	for k := range store.messages {
		keys = append(keys, k)
	}
	return keys
}
```

The default memory store ranges over a map, which does not preserve insertion order.

`fvt_client_test.go:1591-1596`

```go
// ... resume works when there is no limit to inflight messages message ordering is not
// guaranteed. However, with SetMaxResumePubInFlight(1) it is guaranteed ...
```

The repository's own test comment acknowledges the ordering risk.

## Implementation Behavior

Paho can replay stored PUBLISH packets in store iteration order rather than original send order. With the default memory store, that order is not stable.

## Runtime Evidence

What was done:
- Ran `go test -run ^TestReplayOrderOrderedStoreControl$ -count=1 -v` using an ordered store as the control.
- Ran `go test -run ^TestReplayOrderMemoryStoreReproducer$ -count=1 -v` using the default `MemoryStore`.

Observed result: Both tests exited with code `0`. The ordered-store control replayed IDs in ascending original order `[1,2]`. The `MemoryStore` reproducer observed out-of-order PUBLISH replay.

## Inconsistency Reason

The standard requires original send order for resent PUBLISH packets. Paho's default resume order is delegated to a map-backed store that does not preserve that order.

## Impact

QoS 1 and QoS 2 messages can be replayed out of order after reconnect, which can break applications that depend on MQTT's ordered resend guarantee.

## Fix Direction

Persist enough ordering metadata to replay PUBLISH packets in original send order, or make the default store return ordered outbound keys. Add a reconnect replay test that fails when `[1,2]` becomes `[2,1]`.
