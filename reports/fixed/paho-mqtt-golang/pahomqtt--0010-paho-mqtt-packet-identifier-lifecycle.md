# PUBREC with the wrong Packet Identifier drives a mismatched PUBREL

## Problem Description

In a QoS 2 outbound flow, MQTT requires the client's PUBREL to carry the same Packet Identifier as the original PUBLISH. Paho builds PUBREL by copying the identifier from the received PUBREC, without first proving that the PUBREC identifier matches the in-flight PUBLISH.

## Summary

The earlier bounded validation left this as suspected, but a focused `net.Pipe` broker simulation now provides a deterministic runtime counterexample.

## Standard Requirement

Official standard: [MQTT Version 3.1.1](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html)

MQTT 3.1.1 Section 4.3.3 defines the QoS 2 publish handshake. After the sender receives PUBREC, it must send PUBREL for the same Packet Identifier as the original PUBLISH:

```text
It sends a PUBREL packet containing the same Packet Identifier as the original PUBLISH packet.
```

MQTT 3.1.1 Section 2.3.1 also requires Packet Identifiers for QoS > 0 PUBLISH packets to be nonzero and currently unused until the corresponding acknowledgement completes.

## Relevant Source Code

`messageids.go:109-123`

```go
func (mids *messageIds) getID(t tokenCompletor) uint16 {
	...
	if _, ok := mids.index[i]; !ok {
		mids.index[i] = t
		mids.lastIssuedID = i
		return i
	}
```

Normal outbound PUBLISH allocation reserves an identifier.

`net.go:248-252`

```go
case *packets.PubrecPacket:
	logger.Debug("startIncomingComms: received pubrec", slog.Uint64("messageID", uint64(m.MessageID)), slog.String("component", string(NET)))
	prel := packets.NewControlPacket(packets.Pubrel).(*packets.PubrelPacket)
	prel.MessageID = m.MessageID
	output <- incomingComms{outbound: &PacketAndToken{p: prel, t: nil}}
```

The PUBREC handler copies the received identifier into PUBREL.

`packets/pubrel.go:35-43`

```go
func (pr *PubrelPacket) Write(w io.Writer) error {
	pr.FixedHeader.RemainingLength = 2
	packet, err := pr.FixedHeader.pack()
	...
	packet.Write(encodeUint16(pr.MessageID))
```

The writer serializes the copied identifier.

## Implementation Behavior

When the broker sends a PUBREC, Paho emits PUBREL with `prel.MessageID = m.MessageID`. The observed path does not reject a PUBREC whose identifier differs from the identifier in the original QoS 2 PUBLISH.

## Runtime Evidence

What was done:
- Re-ran the focused Go reproducer with `go run .`.
- The reproducer used `net.Pipe` as a fake broker, accepted CONNECT, captured the client's QoS 2 PUBLISH, then sent either a matching PUBREC or a PUBREC with a different Packet Identifier.
- It decoded the resulting PUBREL from the client and compared it with the original PUBLISH identifier.

Observed result: The run exited successfully with `control_passed=true` and `wrong_id_reproduced=true`. In the control case, Paho sent PUBLISH ID `1`, received PUBREC ID `1`, and emitted PUBREL `62020001`. In the reproducer, Paho again sent PUBLISH ID `1`; the fake broker sent PUBREC ID `2`; Paho emitted PUBREL `62020002`. The observed PUBREL matched the wrong PUBREC identifier and did not match the original PUBLISH identifier.

## Inconsistency Reason

The standard ties PUBREL to the original PUBLISH Packet Identifier. Paho ties PUBREL to the received PUBREC identifier, so a malformed or mismatched PUBREC can move the client into a QoS 2 continuation for the wrong identifier.

## Impact

A broker or network peer that sends a mismatched PUBREC can cause the client to emit an invalid PUBREL. This can break QoS 2 delivery state, complete or disturb the wrong in-flight exchange, and reduce interoperability with strict peers.

## Fix Direction

Track the Packet Identifier of each outbound QoS 2 PUBLISH and validate incoming PUBREC against that in-flight state before generating PUBREL. If no matching outbound PUBLISH exists, treat the PUBREC as a protocol error instead of copying its identifier.
