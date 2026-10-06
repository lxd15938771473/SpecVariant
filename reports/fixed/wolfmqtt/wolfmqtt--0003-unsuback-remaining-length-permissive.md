# MQTT 3.1.1 UNSUBACK Remaining Length check is too permissive

## Standard Requirement

Reference: [MQTT v3.1.1 specification, Section 3.11 UNSUBACK and Section 4.8](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html).

```text
byte 2  Remaining Length (2)
Remaining Length field
This is the length of the variable header. For the UNSUBACK Packet this has the value 2.
3.11.2 Variable header
The variable header contains the Packet Identifier ...
3.11.3 Payload
The UNSUBACK Packet has no payload.
```

MQTT 3.1.1 UNSUBACK is therefore the fixed header plus a 2-byte Packet Identifier. `B0 03 00 01 7F` has Remaining Length `3` and carries one extra byte, which is a protocol violation; the receiver should close the Network Connection for such a violating Control Packet.

## Code Comparison

The inbound decoder accepts Remaining Length values greater than or equal to 2:

```c
header_len = MqttDecode_FixedHeader(rx_buf, rx_buf_len, &remain_len,
    MQTT_PACKET_TYPE_UNSUBSCRIBE_ACK, NULL, NULL, NULL);

if (rx_buf_len < header_len + remain_len)
    return MQTT_TRACE_ERROR(MQTT_CODE_ERROR_OUT_OF_BUFFER);

if (remain_len < MQTT_DATA_LEN_SIZE)
    return MQTT_TRACE_ERROR(MQTT_CODE_ERROR_MALFORMED_DATA);

tmp = MqttDecode_Num(rx_payload, &unsubscribe_ack->packet_id, ...);

return header_len + remain_len;
```

The problem is that this code rejects only `remain_len < 2`. In MQTT 3.1.1 mode it does not require `remain_len == 2`. After decoding the 2-byte Packet Identifier, an extra byte is neither parsed nor rejected.

The client dispatch path adds no exact Remaining Length check before accepting the decoded UNSUBACK.

The encoder generates `Remaining Length = 2`, so the issue is limited to inbound UNSUBACK decoding.

## Runtime Evidence

A focused UNSUBACK decode probe was compiled and run with three inputs:

```c
byte valid[] = { 0xB0, 0x02, 0x00, 0x01 };
byte extra[] = { 0xB0, 0x03, 0x00, 0x01, 0x7F };
byte short_len[] = { 0xB0, 0x01, 0x00 };

#ifdef WOLFMQTT_V5
ack.protocol_level = MQTT_CONNECT_PROTOCOL_LEVEL_4;
#endif
rc = MqttDecode_UnsubscribeAck(packet, packet_len, &ack);
```

Observed output:

```text
valid_rl_2 rc=4 packet_id=1
invalid_rl_3_extra_byte rc=5 packet_id=1
invalid_rl_1_short rc=-3 packet_id=0
```

Observed result: `invalid_rl_1_short` is rejected, showing a lower-bound length check exists. However, `invalid_rl_3_extra_byte` returns positive value `5`, meaning the decoder accepted and consumed the five-byte packet even though MQTT 3.1.1 requires UNSUBACK Remaining Length to be exactly `2` and forbids payload.

## Decision

In MQTT 3.1.1 UNSUBACK decoding, reject `remain_len != MQTT_DATA_LEN_SIZE`. MQTT 5.0 can keep its separate reason-code and properties handling.
