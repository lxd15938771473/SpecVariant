# Will Retain can remain set when Will Flag is zero

## Problem Description

Paho can build a CONNECT packet with Will Flag set to 0 and Will Retain set to 1. The reachable case is `SetWill(... retained=true)` followed by `UnsetWill()`: `UnsetWill` clears only `WillEnabled`, leaving `WillRetained` true.

## Summary

The issue is a stale option value that is still serialized into the CONNECT flags byte.

## Standard Requirement

Official standard: [MQTT Version 3.1.1](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html)

MQTT 3.1.1 Sections 3.1.2.5 and 3.1.2.7 require Will fields to be zeroed when Will Flag is zero:

```text
If the Will Flag is set to 0 the Will QoS and Will Retain fields in the Connect Flags MUST be set to zero and the Will Topic and Will Message fields MUST NOT be present in the payload [MQTT-3.1.2-11].
```

```text
If the Will Flag is set to 0, then the Will Retain Flag MUST be set to 0 [MQTT-3.1.2-15].
```

## Relevant Source Code

`options.go:304-328`

```go
func (o *ClientOptions) UnsetWill() *ClientOptions {
	o.WillEnabled = false
	return o
}

func (o *ClientOptions) SetBinaryWill(topic string, payload []byte, qos byte, retained bool) *ClientOptions {
	o.WillEnabled = true
	o.WillTopic = topic
	o.WillPayload = payload
	o.WillQos = qos
	o.WillRetained = retained
	return o
}
```

`UnsetWill` does not clear `WillRetained`.

`message.go:92-130`

```go
m.WillFlag = options.WillEnabled
m.WillRetain = options.WillRetained
```

The CONNECT builder copies `WillRetained` independently of `WillEnabled`.

`packets/connect.go:55-83`

```go
body.WriteByte(boolToByte(c.CleanSession)<<1 |
	boolToByte(c.WillFlag)<<2 |
	c.WillQos<<3 |
	boolToByte(c.WillRetain)<<5 |
	...)
```

The writer serializes the stale retain bit into the flags byte.

## Implementation Behavior

When Will is disabled, Paho omits the Will Topic and Will Message payload fields, but it can still serialize Will Retain as 1.

## Runtime Evidence

What was done:
- Ran a CONNECT control with Will disabled and retain cleared.
- Ran a reproducer where retained Will state was left behind after disabling Will.

Observed result: The control emitted `connect_flags=0x02` with `will_flag=0` and `will_retain=0`. The reproducer emitted `connect_flags=0x22` with `will_flag=0` and `will_retain=1`.

## Inconsistency Reason

MQTT requires Will Retain to be zero whenever Will Flag is zero. Paho keeps a stale retain option and writes it even though Will is disabled.

## Impact

Brokers can reject the CONNECT as malformed, and clients may fail to connect after an otherwise reasonable option sequence.

## Fix Direction

Clear Will QoS, Will Retain, Will Topic, and Will payload in `UnsetWill`, and validate CONNECT flag invariants before writing the packet.
