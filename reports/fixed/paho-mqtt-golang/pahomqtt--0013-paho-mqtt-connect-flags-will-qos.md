# Will QoS can corrupt CONNECT flags

## Problem Description

Paho stores Will QoS as an unrestricted byte and shifts it into the CONNECT flags byte. Values outside 0, 1, and 2 can either encode the forbidden Will QoS value 3 or spill into adjacent flag bits.

## Summary

This is a client-side CONNECT construction issue reachable through the public Will configuration API.

## Standard Requirement

Official standard: [MQTT Version 3.1.1](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html)

MQTT 3.1.1 Section 3.1.2.3 defines the CONNECT flags byte:

```text
Bit 7 6 5 4 3 2 1 0
User Name Flag Password Flag Will Retain Will QoS Will Flag Clean Session Reserved
```

Section 3.1.2.6 limits Will QoS:

```text
If the Will Flag is set to 1, the value of Will QoS can be 0 (0x00), 1 (0x01), or 2 (0x02). It MUST NOT be 3 (0x03) [MQTT-3.1.2-14].
```

## Relevant Source Code

`options.go:319-328`

```go
func (o *ClientOptions) SetBinaryWill(topic string, payload []byte, qos byte, retained bool) *ClientOptions {
	o.WillEnabled = true
	o.WillTopic = topic
	o.WillPayload = payload
	o.WillQos = qos
	o.WillRetained = retained
	return o
}
```

The public API stores the raw QoS byte.

`message.go:92-104`

```go
if options.WillEnabled {
	m.WillQos = options.WillQos
	m.WillTopic = options.WillTopic
	m.WillMessage = options.WillPayload
}
```

The raw value is copied into the CONNECT packet.

`packets/connect.go:55-73`

```go
body.WriteByte(boolToByte(c.CleanSession)<<1 |
	boolToByte(c.WillFlag)<<2 |
	c.WillQos<<3 |
	boolToByte(c.WillRetain)<<5 |
	boolToByte(c.PasswordFlag)<<6 |
	boolToByte(c.UsernameFlag)<<7)
```

CONNECT serialization shifts the whole byte without a range check or mask.

## Implementation Behavior

Valid Will QoS values encode correctly. Invalid values can encode `Will QoS=3` or set unrelated CONNECT flags such as Username, Password, or Will Retain.

## Runtime Evidence

What was done:
- Ran controls for valid Will QoS and CONNECT flag construction.
- Ran reproducers for QoS high bits, QoS value 3, and flag pollution.

Observed result: Valid controls emitted expected flags such as `0x0e` or `0x16`. Reproducers emitted invalid or polluted flags, including `0xe6` with username/password/will-retain bits set, `0x26` with Will Retain set by high bits, and `0x1e` representing forbidden Will QoS 3.

## Inconsistency Reason

MQTT confines Will QoS to bits 4 and 3 and forbids the value 3. Paho shifts an unrestricted byte into the flag field, so invalid Will QoS input can violate both the value domain and adjacent flag assignments.

## Impact

The client can send CONNECT packets whose flags misrepresent authentication fields or Will properties, leading to broker rejection or incorrect session setup.

## Fix Direction

Reject Will QoS values above 2 in `SetWill` / `SetBinaryWill`, and defensively mask or validate `ConnectPacket.WillQos` before serialization.
