# Malformed SUBACK with Remaining Length 1 is accepted

## Summary
- A valid SUBACK must contain a 2-byte Message ID in the variable header. Therefore `Remaining Length=1` is malformed and should be rejected.
- Runtime evidence confirms Paho parses `90 01 02` as `*packets.SubackPacket` with `MessageID=512` and no return codes.

## Standard Requirement

- Standard: [MQTT v3.0/v3.1 specification](https://public.dhe.ibm.com/software/dw/webservices/ws-mqtt/mqtt-v3r1.html)
- Sections: `2.1 Fixed header`, `3.9 SUBACK - Subscription acknowledgement`

```text
Remaining Length ... Represents the number of bytes remaining within the current message,
including data in the variable header and the payload.

The variable header contains the Message ID for the SUBSCRIBE message that is being acknowledged.
byte 1 Message ID MSB
byte 2 Message ID LSB

The payload contains a vector of granted QoS levels.
```

Thus SUBACK `Remaining Length` covers the 2-byte variable header plus the granted-QoS payload. A SUBACK with `Remaining Length=1` cannot contain the required Message ID and is not a valid MQTT 3.0 SUBACK.

## Relevant Source Code

`implementions/paho.mqtt.golang-master/packets/packets.go:146`

```go
packetBytes := make([]byte, fh.RemainingLength)
n, err := io.ReadFull(r, packetBytes)
if err != nil {
	return nil, err
}
if n != fh.RemainingLength {
	return nil, errors.New("failed to read expected data")
}

err = cp.Unpack(bytes.NewBuffer(packetBytes))
return cp, err
```

`ReadPacket` correctly reads exactly the declared `RemainingLength`, then passes that limited buffer to packet-specific `Unpack`.

`implementions/paho.mqtt.golang-master/packets/suback.go:55`

```go
func (sa *SubackPacket) Unpack(b io.Reader) error {
	var qosBuffer bytes.Buffer
	var err error
	sa.MessageID, err = decodeUint16(b)
	if err != nil {
		return err
	}

	_, err = qosBuffer.ReadFrom(b)
	if err != nil {
		return err
	}
	sa.ReturnCodes = qosBuffer.Bytes()

	return nil
}
```

`implementions/paho.mqtt.golang-master/packets/packets.go:297`

```go
func decodeUint16(b io.Reader) (uint16, error) {
	num := make([]byte, 2)
	_, err := b.Read(num)
	if err != nil {
		return 0, err
	}
	return binary.BigEndian.Uint16(num), nil
}
```

`decodeUint16` ignores the number of bytes read. With a one-byte buffer, `Read` can return `n=1, err=nil`; the second byte stays zero, so `0x02` becomes `0x0200`.

## Implementation Behavior

For input `90 01 02`:

- fixed header says packet type is SUBACK and `Remaining Length=1`;
- `ReadPacket` reads one body byte: `02`;
- `SubackPacket.Unpack` calls `decodeUint16`;
- `decodeUint16` accepts the one-byte short read and returns `MessageID=0x0200`;
- `ReturnCodes` becomes empty, and no parse error is returned.

## Inconsistency Reason

The standard requires SUBACK to carry a complete 2-byte Message ID in its variable header. Paho accepts a SUBACK whose declared body length is only one byte because its shared uint16 decoder does not require two bytes. This converts a malformed packet into a normal `SubackPacket`, which is inconsistent with the SUBACK wire format.

## Runtime Evidence

A focused packet-level probe was run. It compared a valid SUBACK positive control against a malformed SUBACK whose Remaining Length was only one byte.

Command:

```powershell
go test . -run '^Test(SubackRemainingLengthIncludesMessageID|SubackRemainingLengthOneAccepted)$' -count=1 -v
```

```text
=== RUN   TestSubackRemainingLengthIncludesMessageID
    packet_probe_test.go:282: standard SUBACK encoded Remaining Length=3; payload-only malformed SUBACK was accepted as *packets.SubackPacket
--- PASS: TestSubackRemainingLengthIncludesMessageID (0.00s)
=== RUN   TestSubackRemainingLengthOneAccepted
    packet_probe_test.go:298: malformed SUBACK 900102 accepted with short-read MessageID=512 ReturnCodes=[]
--- PASS: TestSubackRemainingLengthOneAccepted (0.00s)
PASS
ok  	speclitmus-mqtt30-paho-packet-probe	0.019s
```

Observed result: the valid SUBACK control passed, and the malformed `90 01 02` packet was accepted as `*packets.SubackPacket` with `MessageID=512` and no return codes.

## Impact

Malformed SUBACK packets can pass packet decoding instead of being rejected. Because `decodeUint16` is shared, the same short-read pattern may affect other packet types that parse a 16-bit field from a bounded `RemainingLength` body.

## Fix Direction

Use `io.ReadFull` in `decodeUint16` and return an error unless exactly two bytes are read. SUBACK parsing should propagate that error, causing `Remaining Length<2` packets to fail decoding.
