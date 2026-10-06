# SUBACK reserved return code is exposed

## Problem Description

MQTT 3.1.1 assigns only SUBACK return codes `0x00`, `0x01`, `0x02`, and `0x80`. Paho stores every byte from the SUBACK payload and exposes it through `SubscribeToken.Result()` without rejecting reserved values.

## Summary

The runtime reproducer observed reserved return code `0x03` being exposed as a successful subscription result.

## Standard Requirement

Official standard: [MQTT Version 3.1.1](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html)

MQTT 3.1.1 Section 3.8.4 says:

```text
This return code MUST either show the maximum QoS that was granted for that Subscription or indicate that the subscription failed.
```

```text
SUBACK return codes other than 0x00, 0x01, 0x02 and 0x80 are reserved and MUST NOT be used.
```

## Relevant Source Code

`packets/suback.go:55-69`

```go
func (sa *SubackPacket) Unpack(b io.Reader) error {
	var qosBuffer bytes.Buffer
	var err error
	sa.MessageID, err = decodeUint16(b)
	...
	_, err = qosBuffer.ReadFrom(b)
	...
	sa.ReturnCodes = qosBuffer.Bytes()
	return nil
}
```

The parser copies all return-code bytes without validating their values.

`net.go:223-232`

```go
if len(m.ReturnCodes) != len(t.subs) {
	token.setError(ErrMalformedSuback)
	c.freeID(m.MessageID)
	output <- incomingComms{err: ErrMalformedSuback}
	continue
}
for i, qos := range m.ReturnCodes {
	t.subResult[t.subs[i]] = qos
}
```

The receive loop validates count but not whether each return code is assigned.

`token.go:176-191`

```go
func (s *SubscribeToken) Result() map[string]byte {
	s.m.RLock()
	defer s.m.RUnlock()
	return s.subResult
}
```

Reserved bytes become visible to application code as results.

## Implementation Behavior

Paho accepts a SUBACK payload containing `0x03` and records that value as the subscription result instead of treating the SUBACK as malformed.

## Runtime Evidence

What was done:
- Ran `go test -run ^TestSubackValidReturnCodeControl$ -count=1 -v` with an assigned SUBACK return code.
- Ran `go test -run ^TestSubackReservedReturnCodeReproducer$ -count=1 -v` with reserved return code `0x03`.

Observed result: Both tests exited with code `0`. The valid return code mapped normally, and reserved `0x03` was also exposed through the subscription result instead of causing an error.

## Inconsistency Reason

The standard reserves all SUBACK return codes except `0x00`, `0x01`, `0x02`, and `0x80`. Paho only checks payload length and accepts unassigned values.

## Impact

Application code can receive impossible subscription results, and strict broker/client interop tests can fail because the client does not reject malformed SUBACK packets.

## Fix Direction

Validate every SUBACK return code before updating `SubscribeToken.Result()`. Reject reserved values as malformed SUBACK input and close the connection through the existing protocol-error path.
