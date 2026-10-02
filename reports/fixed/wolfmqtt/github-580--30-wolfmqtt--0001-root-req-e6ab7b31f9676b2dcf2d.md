# MQTT v3.x client accepts reserved packet type 15 as AUTH

## Summary

Verdict: `issue_found`

When wolfMQTT is built with MQTT v5 support but the client session is `protocol_level=4` (MQTT v3.1.1), `MqttClient_WaitMessage_ex()` accepts a packet with fixed-header type `15` (`0xF0 0x00`) and decodes it as `AUTH`. In MQTT v3.0 and v3.1.1, packet type `15` is reserved/forbidden; `AUTH` is only defined by MQTT v5.

This is a client receive-path issue. The broker path closes unhandled type `15` packets after dispatch, so the broker code is not the primary evidence for this report.

## Standard Requirement

Official references:

- [MQTT v3.0/v3.1 specification](https://public.dhe.ibm.com/software/dw/webservices/ws-mqtt/mqtt-v3r1.html)
- [MQTT v3.1.1 specification](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html)
- [MQTT v5.0 specification](https://docs.oasis-open.org/mqtt/mqtt/v5.0/os/mqtt-v5.0-os.html)

```text
Message Type
Represented as a 4-bit unsigned value. The enumerations for this version of the protocol are shown in the table below.

Reserved 0 Reserved
CONNECT 1
...
DISCONNECT 14
Reserved 15 Reserved
```

MQTT v3.1.1 has the same control-packet-type meaning for this case: type `15` is `Reserved` and `Forbidden`. MQTT v5 changes type `15` to `AUTH`; therefore an implementation that supports both v3.1.1 and v5 must gate AUTH parsing on the negotiated protocol level.

## Relevant Source Code

`implementions/wolfMQTT-master/wolfmqtt/mqtt_packet.h:263-280`

```c
typedef enum _MqttPacketType {
    MQTT_PACKET_TYPE_RESERVED = 0,
    MQTT_PACKET_TYPE_CONNECT = 1,
    ...
    MQTT_PACKET_TYPE_DISCONNECT = 14,
    MQTT_PACKET_TYPE_AUTH = 15,             /* Authentication (MQTT 5) */
    MQTT_PACKET_TYPE_ANY = 16
} MqttPacketType;
```

The enum itself is acceptable for an MQTT v5 build, but later receive-side code must not treat `15` as valid for a v3.1.1 session.

`implementions/wolfMQTT-master/src/mqtt_client.c:861-887`

```c
header = (MqttPacket*)rx_buf;
packet_type = (MqttPacketType)MQTT_PACKET_TYPE_GET(header->type_flags);
...
switch (packet_type) {
case MQTT_PACKET_TYPE_CONNECT_ACK:
    ...
    p_connect_ack->protocol_level = client->protocol_level;
    rc = MqttDecode_ConnectAck(rx_buf, rx_len, p_connect_ack);
```

This dispatcher reads the packet type from the fixed header. Some packet decoders receive `client->protocol_level`, but the packet-type dispatch itself does not reject `AUTH` when `client->protocol_level < 5`.

`implementions/wolfMQTT-master/src/mqtt_client.c:1047-1068`

```c
case MQTT_PACKET_TYPE_AUTH:
{
#ifdef WOLFMQTT_V5
    MqttAuth auth, *p_auth = &auth;
    ...
    rc = MqttDecode_Auth(rx_buf, rx_len, p_auth);
    ...
#else
    rc = MQTT_TRACE_ERROR(MQTT_CODE_ERROR_PACKET_TYPE);
#endif
    break;
}
```

With `WOLFMQTT_V5`, `AUTH` is accepted based on compile-time support. There is no runtime check such as `client->protocol_level >= MQTT_CONNECT_PROTOCOL_LEVEL_5`.

`implementions/wolfMQTT-master/src/mqtt_client.c:1629-1656` and `:1694-1709`

```c
rc = MqttPacket_Read(client, client->rx_buf, client->rx_buf_len, timeout_ms);
...
rc = MqttClient_DecodePacket(client, client->rx_buf,
    client->packet.buf_len, NULL, &packet_type, &packet_qos,
    &packet_id, 1);
...
if ((wait_type == MQTT_PACKET_TYPE_ANY ||
     wait_type == packet_type ||
     (MqttIsPubRespPacket(packet_type) &&
      MqttIsPubRespPacket(wait_type))) &&
    (wait_packet_id == 0 || wait_packet_id == packet_id))
{
    use_packet_obj = packet_obj;
    if (packet_type == wait_type || wait_type == MQTT_PACKET_TYPE_ANY) {
        waitMatchFound = 1;
    }
}
```

`MqttClient_WaitMessage_ex()` waits for `MQTT_PACKET_TYPE_ANY`, so a decoded `AUTH` packet is treated as a matching incoming message.

`implementions/wolfMQTT-master/src/mqtt_packet.c:4132-4148`

```c
int MqttDecode_Auth(byte *rx_buf, int rx_buf_len, MqttAuth *auth)
{
    ...
    header_len = MqttDecode_FixedHeader(rx_buf, rx_buf_len, &remain_len,
                  MQTT_PACKET_TYPE_AUTH, NULL, NULL, NULL);
    if (header_len < 0) {
        return header_len;
    }
```

`MqttDecode_Auth()` has no protocol-level argument, so it cannot distinguish MQTT v5 from v3.1.1.

## Implementation Behavior

In an MQTT v5-capable build, a v3.1.1 client can still have `client->protocol_level = MQTT_CONNECT_PROTOCOL_LEVEL_4`. wolfMQTT uses this mode in real examples, e.g. `implementions/wolfMQTT-master/examples/azure/azureiothub.c:318-320`.

For such a v3.1.1 client, receiving `0xF0 0x00` should be rejected because the high nibble is packet type `15`. Instead, the receive path decodes it as `MQTT_PACKET_TYPE_AUTH` and returns success.

## Inconsistency Reason

The standard requires type `15` to be reserved/forbidden in MQTT v3.x. wolfMQTT compiles `AUTH` support for MQTT v5, but the client receive dispatcher accepts `AUTH` solely because `WOLFMQTT_V5` is enabled. It does not gate `AUTH` decoding on `client->protocol_level >= 5`, so an MQTT v3.1.1 session accepts a control packet type that is not defined for that protocol version.

## Runtime Evidence

A focused protocol-level packet probe was compiled and run. First, a positive-control v3.1.1 CONNECT decode was run to confirm the packet-probe harness accepted valid v3.1.1 traffic:

```text
runtime_tests/packet_probe/probe.exe connect-v311-positive
```

Observed result: exit code `0`.

Then the type-15 reproducer was run against a v3.1.1 client session:

```text
runtime_tests/type15_protocol_probe.exe
```

Observed output:

```text
MqttClient_DecodePacket: Rc 2, Len 2, Type Auth (15), ID 0, QoS 0, doProps 1
MqttClient_WaitType: rc 0, state 4-0-0
waitmessage_type15_v31 rc=0 in_pos=2 reason=0
auth_send_type15_v31 rc=-8 out_len=2 first=0xF0
```

Probe exit code: `2`. In this probe, nonzero means the v3.1.1 path accepted or emitted packet type `15`; the first three lines above are the decisive receive-path evidence.

Additional controls were run and passed:

```text
runtime_tests/packet_probe/probe.exe connect-mqtt30-decode-rejected  -> 0
runtime_tests/packet_probe/probe.exe protocol-level3-encode-rejected -> 0
runtime_tests/packet_probe/probe.exe connect-v311-positive           -> 0
```

These controls show that the harness can reject invalid MQTT 3.0 CONNECT/protocol-level combinations while the v3.1.1 receive path still accepts packet type `15` as `AUTH`.

## Impact

A v3.1.1 client may treat a reserved/forbidden control packet as a valid MQTT v5 `AUTH` packet. This weakens protocol-version separation and can let malformed or cross-version traffic reach normal message handling instead of being rejected.

## Fix Direction

Reject `MQTT_PACKET_TYPE_AUTH` whenever `client->protocol_level < MQTT_CONNECT_PROTOCOL_LEVEL_5`. The check should be in the client receive dispatch before calling `MqttDecode_Auth()`. Similar helpers that validate fixed-header type `15` should either accept a protocol-level argument or be used only after a version-aware check.
