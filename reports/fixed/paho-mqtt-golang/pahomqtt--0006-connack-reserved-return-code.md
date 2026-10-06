# CONNACK reserved return codes are surfaced

## Problem Description

Paho decodes reserved CONNACK return codes and returns them to the client handshake as ordinary parsed values. MQTT 3.1.1 reserves values 0x06 through 0xff.

## Summary

The parser stores the return-code byte directly and the handshake returns it without an allowed-value check.

## Standard Requirement

Official standard: [MQTT Version 3.1.1](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html)

MQTT 3.1.1 Section 3.2.2.3 lists valid Connect Return codes and reserves the rest:

```text
6-255 Reserved for future use
```

## Relevant Source Code

`packets/connack.go:56-65`

```go
flags, err := decodeByte(b)
...
ca.ReturnCode, err = decodeByte(b)
return err
```

The return code is copied directly.

`net.go:91-113`

```go
msg, ok := ca.(*packets.ConnackPacket)
...
return msg.ReturnCode, msg.SessionPresent, nil
```

The handshake returns the reserved value to the caller.

## Implementation Behavior

A reserved return code is treated as a successfully decoded CONNACK rather than a malformed broker response.

## Runtime Evidence

What was done:
- Ran a valid CONNACK return-code control.
- Ran a reproducer with CONNACK return code `0x06`.

Observed result: The valid control was accepted. The reproducer also parsed successfully and exposed return code `0x06`.

## Inconsistency Reason

MQTT assigns no meaning to CONNACK return codes 0x06 through 0xff. Paho still surfaces one of those reserved values as a parsed result.

## Impact

Client code can receive an undefined connection result and make decisions based on a value that MQTT 3.1.1 does not permit.

## Fix Direction

Validate CONNACK return codes against the MQTT 3.1.1 set 0x00 through 0x05 and reject reserved values as protocol errors.
