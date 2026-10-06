# MQTT 3.0 UNSUBACK accepts payload bytes

## Summary

Verdict: `issue_found`

MQTT 3.0 `UNSUBACK` has a fixed Remaining Length of `2` and no payload. wolfMQTT's MQTT 3.x `UNSUBACK` decoder rejects Remaining Length values below `2`, but it does not reject values above `2`. As a result, the packet `0xB0 0x03 0x00 0x07 0x00` is accepted as a valid `UNSUBACK`, even though the final byte is an invalid payload byte.

## Standard Requirement

Reference: [MQTT v3.0/v3.1 specification, Section 3.11, UNSUBACK - Unsubscribe acknowledgment](https://public.dhe.ibm.com/software/dw/webservices/ws-mqtt/mqtt-v3r1.html).

The fixed header defines byte 2 as:

```text
Remaining length (2)
```

The variable header is:

```text
The variable header contains the Message ID for the UNSUBSCRIBE message that is being acknowledged.
```

The payload rule is:

```text
There is no payload.
```

Therefore, a valid MQTT 3.0 `UNSUBACK` body contains only the 2-byte Message ID. Remaining Length must be exactly `2`; no extra payload byte is allowed.

## Relevant Source Code

`implementions/wolfMQTT-master/src/mqtt_packet.c:3577`

```c
header_len = MqttDecode_FixedHeader(rx_buf, rx_buf_len, &remain_len,
    MQTT_PACKET_TYPE_UNSUBSCRIBE_ACK, NULL, NULL, NULL);
```

`implementions/wolfMQTT-master/src/mqtt_packet.c:3590`

```c
if (remain_len < MQTT_DATA_LEN_SIZE) {
    return MQTT_TRACE_ERROR(MQTT_CODE_ERROR_MALFORMED_DATA);
}
```

`implementions/wolfMQTT-master/src/mqtt_packet.c:3597`

```c
tmp = MqttDecode_Num(rx_payload, &unsubscribe_ack->packet_id,
        (word32)(rx_buf_len - (rx_payload - rx_buf)));
```

`implementions/wolfMQTT-master/src/mqtt_packet.c:3671`

```c
return header_len + remain_len;
```

The encoder writes the fixed MQTT 3.x size:

`implementions/wolfMQTT-master/src/mqtt_packet.c:3695`

```c
remain_len = MQTT_DATA_LEN_SIZE; /* For packet_id */
```

## Implementation Behavior

`MqttDecode_UnsubscribeAck` does not enforce `remain_len == MQTT_DATA_LEN_SIZE` on the MQTT 3.x path. It accepts any `remain_len >= 2`, decodes the first two bytes as the Packet ID, and returns success using the declared Remaining Length.

## Inconsistency Reason

The standard requires MQTT 3.0 `UNSUBACK` to have no payload and a fixed Remaining Length of `2`. The implementation only checks the lower bound, so a packet with Remaining Length `3` is successfully decoded and the extra byte is swallowed into the packet.

## Runtime Evidence

A focused packet-format probe was compiled and run against the `UNSUBACK` Remaining Length and no-payload constraints.

Build and run summary:

```text
build_exit=0
positive: probe.exe unsuback-positive -> exit 0
reproducer: probe.exe unsuback-extra-accepted -> exit 0
stdout: UNSUBACK with Remaining Length 3 accepted rc=5
```

The reproducer input was:

```c
byte raw[] = {0xB0, 0x03, 0x00, 0x07, 0x00};
int rc = MqttDecode_UnsubscribeAck(raw, (int)sizeof(raw), &ack);
```

Observed result: the valid `UNSUBACK` positive control passed, and the malformed `UNSUBACK` with Remaining Length `3` was accepted with return code `5`. The return value covers the whole five-byte packet, including the extra byte that should not exist in MQTT 3.0.

## Impact

A peer can send an MQTT 3.0 `UNSUBACK` with extra payload bytes and the client will still process it as a valid acknowledgment. This weakens frame-format validation and can hide protocol state or stream parsing errors.

## Fix Direction

Reject any MQTT 3.x `UNSUBACK` whose Remaining Length is not exactly `2`:

```c
if (remain_len != MQTT_DATA_LEN_SIZE) {
    return MQTT_TRACE_ERROR(MQTT_CODE_ERROR_MALFORMED_DATA);
}
```

Keep MQTT 5 handling separate, because MQTT 5 acknowledgments can include a Reason Code and Properties.
