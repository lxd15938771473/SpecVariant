# Inbound PUBLISH without a handler is not acknowledged

## Problem Description

MQTT 3.1.1 requires a client to acknowledge received PUBLISH packets according to QoS even if the application does not process the message. Paho only sends the acknowledgement through the handler/default-handler path, so a QoS PUBLISH can remain unacknowledged when no handler is installed.

## Summary

The runtime reproducer observed no PUBACK or PUBREC before the deadline when the client had no matching route and no default publish handler.

## Standard Requirement

Official standard: [MQTT Version 3.1.1](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html)

MQTT 3.1.1 Section 4.5 says:

```text
The Client MUST acknowledge any Publish Packet it receives according to the applicable QoS rules regardless of whether it elects to process the Application Message that it contains.
```

## Relevant Source Code

`router.go:188-239`

```go
for message := range messages {
	sent := false
	m := messageFromPublish(message, ackFunc(sendAck, client.persist, message, r.logger))
	for e := r.routes.Front(); e != nil; e = e.Next() {
		if e.Value.(*route).match(message.TopicName) {
			...
			hd(client, m)
			if !client.options.AutoAckDisabled {
				m.Ack()
			}
			sent = true
		}
	}
	if !sent {
		if r.defaultHandler != nil {
			...
		} else {
			r.logger.Debug("matchAndDispatch received message and no handler was available. Message will NOT be acknowledged.", ...)
		}
	}
}
```

`net.go:468-491`

```go
func ackFunc(sendAck func(*PacketAndToken), persist Store, packet *packets.PublishPacket, logger *slog.Logger) func() {
	return func() {
		switch packet.Qos {
		case 2:
			pr := packets.NewControlPacket(packets.Pubrec).(*packets.PubrecPacket)
			pr.MessageID = packet.MessageID
			sendAck(&PacketAndToken{p: pr, t: nil})
		case 1:
			pa := packets.NewControlPacket(packets.Puback).(*packets.PubackPacket)
			pa.MessageID = packet.MessageID
			sendAck(&PacketAndToken{p: pa, t: nil})
		}
	}
}
```

ACK generation exists, but it is only called from the message handling path.

`README.md:114-118`

```text
If there is no handler (or DefaultPublishHandler) then inbound messages will not be acknowledged.
```

The documented behavior matches the code path.

## Implementation Behavior

When no route or default handler exists, the router logs that the message will not be acknowledged. That means the client can choose not to process the application message and also skip the required QoS acknowledgement.

## Runtime Evidence

What was done:
- Ran `go test -run ^TestInboundPublishQos1AckControl$ -count=1 -v` with a handler installed.
- Ran `go test -run ^TestInboundPublishQos1NoHandlerReproducer$ -count=1 -v` with no matching route and no default handler.

Observed result: Both tests exited with code `0`. The handler control emitted the expected PUBACK. The no-handler reproducer observed no PUBACK or PUBREC before the broker-side deadline.

## Inconsistency Reason

The standard separates QoS acknowledgement from application processing. Paho ties acknowledgement to handler dispatch, so the no-handler case violates the required acknowledgement behavior.

## Impact

Brokers can retain inflight QoS messages and redeliver them because the client never acknowledges receipt. This can cause duplicate delivery, inflight-limit pressure, and interoperability failures.

## Fix Direction

Send the QoS acknowledgement even when no application handler accepts the message. Keep `AutoAckDisabled` semantics explicit, but do not make missing handlers suppress protocol acknowledgements by default.
