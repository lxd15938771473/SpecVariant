# PUBREC order can change in async handler mode

## Problem Description

MQTT 3.1.1 requires PUBREC packets for QoS 2 messages to be sent in the order of the corresponding received PUBLISH packets. Paho's asynchronous handler mode can acknowledge the second QoS 2 PUBLISH before the first if the second handler completes earlier.

## Summary

The runtime reproducer observed default mode PUBREC order `[1,2]` and asynchronous mode PUBREC order `[2,1]`.

## Standard Requirement

Official standard: [MQTT Version 3.1.1](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html)

MQTT 3.1.1 Section 4.6 says:

```text
It MUST send PUBREC packets in the order in which the corresponding PUBLISH packets were received (QoS 2 messages).
```

## Relevant Source Code

`options.go:245-255`

```go
// If set to false (recommended),
// this flag indicates that messages can be delivered asynchronously
// from the client to the application and possibly arrive out of order.
// Specifically, the message handler is called in its own go routine.
func (o *ClientOptions) SetOrderMatters(order bool) *ClientOptions {
```

`router.go:198-208`

```go
if order {
	handlers = append(handlers, e.Value.(*route).callback)
} else {
	hd := e.Value.(*route).callback
	go func() {
		hd(client, m)
		if !client.options.AutoAckDisabled {
			m.Ack()
		}
	}()
}
```

`net.go:472-477`

```go
case 2:
	pr := packets.NewControlPacket(packets.Pubrec).(*packets.PubrecPacket)
	pr.MessageID = packet.MessageID
	sendAck(&PacketAndToken{p: pr, t: nil})
```

QoS 2 ACK creates PUBREC after the handler calls `Ack`.

## Implementation Behavior

Asynchronous handler execution lets the second handler call `Ack` before the first. Since PUBREC is generated at `Ack` time, the outbound PUBREC order can differ from inbound PUBLISH receive order.

## Runtime Evidence

What was done:
- Ran `go test -run ^TestPubrecOrderDefaultControl$ -count=1 -v` with default ordered handling.
- Ran `go test -run ^TestPubrecOrderAsyncReproducer$ -count=1 -v` with asynchronous handler mode.

Observed result: Both tests exited with code `0`. The default-order control emitted PUBREC IDs `[1,2]`. The asynchronous-mode reproducer emitted PUBREC IDs `[2,1]`.

## Inconsistency Reason

The standard orders PUBREC by PUBLISH receive order. Paho's asynchronous mode lets callback completion order determine PUBREC order.

## Impact

Peers can observe QoS 2 acknowledgement packets out of order, weakening MQTT's ordered delivery guarantees and causing interoperability failures with strict brokers.

## Fix Direction

Serialize QoS 2 protocol acknowledgements in receive order even when application handlers run concurrently. Add a regression test with deliberately staggered handler completion.
