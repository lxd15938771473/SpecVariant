# CONNECT can emit zero ClientId with CleanSession false

## Problem Description

A caller can configure an empty ClientId together with `CleanSession=false`. Paho then writes that CONNECT packet through the normal client handshake path instead of rejecting it or forcing CleanSession to 1.

## Summary

`ConnectPacket.Validate()` contains the correct check, but the reachable client send path writes the packet without calling it.

## Standard Requirement

Official standard: [MQTT Version 3.1.1](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html)

MQTT 3.1.1 Section 3.1.3.1 defines the zero-byte ClientId rule:

```text
If the Client supplies a zero-byte ClientId, the Client MUST also set CleanSession to 1 [MQTT-3.1.3-7].
```

## Relevant Source Code

`options.go:201-242`

```go
func (o *ClientOptions) SetClientID(id string) *ClientOptions {
	o.ClientID = id
	return o
}

func (o *ClientOptions) SetCleanSession(clean bool) *ClientOptions {
	o.CleanSession = clean
	return o
}
```

The two options are stored independently.

`message.go:92-99`

```go
m.CleanSession = options.CleanSession
m.ClientIdentifier = options.ClientID
```

The CONNECT builder copies both values into the packet.

`net.go:58-84`

```go
if err := cm.Write(conn); err != nil {
	return packets.ErrNetworkError, false, err
}
```

The handshake writes CONNECT directly.

`packets/connect.go:143-167`

```go
if len(c.ClientIdentifier) == 0 && !c.CleanSession {
	return ErrRefusedIDRejected
}
```

The check exists in `Validate`, but the send path above does not invoke it.

## Implementation Behavior

The client accepts the option combination and serializes it. Validation is present in a packet method but disconnected from the normal CONNECT write path.

## Runtime Evidence

What was done:
- Ran a valid CONNECT control with empty ClientId and CleanSession set.
- Ran a reproducer with empty ClientId and CleanSession cleared through the reachable client construction path.

Observed result: The control emitted CONNECT with CleanSession=1 and an empty ClientId. The reproducer emitted CONNECT with CleanSession=0 and an empty ClientId.

## Inconsistency Reason

MQTT permits a zero-byte ClientId only when CleanSession is 1. Paho can send the forbidden pairing because client options are copied and written without the available validation check.

## Impact

Brokers can reject the CONNECT packet as malformed, and users may see connection failures from a configuration that the client could have rejected locally.

## Fix Direction

Call CONNECT validation before writing the packet, or enforce the zero ClientId/CleanSession invariant when options are set or when the CONNECT packet is built.
