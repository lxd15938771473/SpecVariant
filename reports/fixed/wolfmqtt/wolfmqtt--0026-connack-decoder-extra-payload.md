# MQTT 3.1.1 CONNACK decoder accepts extra payload

## Summary

wolfMQTT's CONNACK encoder correctly emits a no-payload MQTT 3.1.1 packet, but the receive-side `MqttDecode_ConnectAck` accepts a CONNACK with `Remaining Length > 2` and includes the extra byte in the decoded packet length.

## Standard Requirement

Reference: [MQTT v3.1.1 specification, Sections 3.2.1 and 3.2.3](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html).

```text
For the CONNACK Packet this has the value 2.
```

```text
The CONNACK Packet has no payload.
```

Therefore, MQTT 3.1.1 CONNACK Remaining Length must be exactly `2`, containing only Connect Acknowledge Flags and Connect Return code. Any content beyond those two bytes is forbidden payload.

## Relevant Source Code

`src/mqtt_packet.c:2053-2073`

```c
header_len = MqttDecode_FixedHeader(rx_buf, rx_buf_len, &remain_len,
    MQTT_PACKET_TYPE_CONNECT_ACK, NULL, NULL, NULL);
...
if (remain_len < 2) {
    return MQTT_TRACE_ERROR(MQTT_CODE_ERROR_MALFORMED_DATA);
}
...
connect_ack->flags = *rx_payload++;
connect_ack->return_code = *rx_payload++;
```

`src/mqtt_packet.c:2090-2126` checks that MQTT 5 properties consume the packet end, but only when protocol level is MQTT 5 or later. The MQTT 3.1.1 path has no `remain_len == 2` or "no leftover bytes" check.

`src/mqtt_packet.c:2216-2251` encodes CONNACK with exactly two variable-header bytes:

```c
remain_len = 2; /* flags + return code */
...
*tx_payload++ = connect_ack->flags;
*tx_payload++ = connect_ack->return_code;
```

`src/mqtt_client.c:875-887` uses this decoder when the client receives server CONNACK, so the permissive behavior affects the client connection path.

## Implementation Behavior

- Valid packet `20 02 00 00` is accepted and returns `rc=4`.
- Invalid packet `20 03 00 00 ff` is also accepted and returns `rc=5`.
- The second packet declares Remaining Length `3`, one byte more than MQTT 3.1.1 allows; that extra byte should cause a malformed/protocol error.

## Inconsistency Reason

MQTT 3.1.1 requires CONNACK to have no payload and fixed `Remaining Length=2`. The implementation checks only `remain_len < 2`, then returns `header_len + remain_len`. A CONNACK with `Remaining Length=3` is therefore accepted as a complete valid packet.

## Runtime Evidence

A direct CONNACK decode probe was compiled and run. It decoded one valid CONNACK positive control and one CONNACK with an extra payload byte.

Observed output:

```text
valid_connack rc=4 flags=0 return_code=0
extra_payload_connack rc=5 flags=0 return_code=0
OBSERVED_ISSUE: MQTT 3.1.1 CONNACK with payload was accepted
```

Observed result: the valid CONNACK succeeded, and the malformed `20 03 00 00 ff` packet also succeeded instead of being rejected.

## Impact

The client can accept a malformed server CONNACK and treat a payload-bearing MQTT 3.1.1 CONNACK as a connection response, without closing the connection for protocol error.

## Fix Direction

In `MqttDecode_ConnectAck`, require `remain_len == 2` for MQTT 3.1.1. Only allow and parse additional properties when `protocol_level >= MQTT_CONNECT_PROTOCOL_LEVEL_5`.
