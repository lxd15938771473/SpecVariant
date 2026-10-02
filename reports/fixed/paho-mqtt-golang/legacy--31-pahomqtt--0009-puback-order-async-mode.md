# PUBACK order can change in async handler mode

## Problem Description

MQTT 3.1.1 requires PUBACK packets for QoS 1 messages to be sent in the order of the corresponding received PUBLISH packets. When Paho runs handlers asynchronously, ACK generation happens after each handler completes, so PUBACK order can follow callback timing instead of receive order.

## Summary

The runtime reproducer observed default mode ACK order `[1,2]` and asynchronous mode ACK order `[2,1]`.

## Standard Requirement

Official standard: [MQTT Version 3.1.1](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html)

MQTT 3.1.1 Section 4.6 says:

```text
It MUST send PUBACK packets in the order in which the corresponding PUBLISH packets were received (QoS 1 messages).
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

When ordering is disabled, ACK is called inside a goroutine after the handler returns.

`net.go:479-486`

```go
case 1:
	pa := packets.NewControlPacket(packets.Puback).(*packets.PubackPacket)
	pa.MessageID = packet.MessageID
	persistOutbound(persist, pa, logger)
	sendAck(&PacketAndToken{p: pa, t: nil})
```

ACK for QoS 1 creates PUBACK for the message that finished handling.

## Implementation Behavior

With ordered handling enabled, handlers and ACKs run serially. With `SetOrderMatters(false)`, slower handling of the first PUBLISH allows the second PUBLISH to be acknowledged first.

## Runtime Evidence

What was done:
- Ran `go test -run ^TestPubackOrderDefaultControl$ -count=1 -v` with default ordered handling.
- Ran `go test -run ^TestPubackOrderAsyncReproducer$ -count=1 -v` with asynchronous handler mode.

Observed result: Both tests exited with code `0`. The default-order control emitted PUBACK IDs `[1,2]`. The asynchronous-mode reproducer emitted PUBACK IDs `[2,1]`.

## Inconsistency Reason

The standard orders PUBACK by PUBLISH receive order. Paho's asynchronous mode lets handler completion order determine ACK order.

## Impact

Peers can observe PUBACK responses out of order for QoS 1 messages, which violates MQTT ordering requirements and can confuse strict broker implementations or test harnesses.

## Fix Direction

Keep application callbacks asynchronous if desired, but serialize protocol ACK emission in receive order. Add a regression test where the first handler blocks briefly and the second completes first.
