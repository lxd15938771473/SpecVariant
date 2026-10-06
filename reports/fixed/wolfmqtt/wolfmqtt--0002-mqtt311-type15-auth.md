# MQTT 3.1.1 AUTH/type 15 accepted by v5-enabled client

Scope is narrowed to the client inbound path when wolfMQTT is built with `WOLFMQTT_V5`. This is not a default non-v5 build issue and not a broker-path issue.

MQTT 3.1.1 forbids Control Packet type `15`. In a v5-enabled build, wolfMQTT can still run a client session with `protocol_level=4` for MQTT 3.1.1, but when it receives an AUTH packet `F0 00`, it follows the MQTT 5 AUTH decode path, returns success, and leaves the client connected.

## Standard

Reference: [MQTT v3.1.1 specification, Sections 2.2.1 and 4.8](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html).

MQTT 3.1.1 Section 2.2.1 defines the high four bits of the fixed-header first byte as the Control Packet type:

```text
Position: byte 1, bits 7-4.
Represented as a 4-bit unsigned value, the values are listed in Table 2.1 - Control packet types.
...
DISCONNECT 14 Client to Server Client is disconnecting
Reserved 15 Forbidden Reserved
```

Section 4.8 requires the receiver to close the connection on protocol violation:

```text
[MQTT-4.8.0-1] Unless stated otherwise, if either the Server or Client encounters a protocol violation, it MUST close the Network Connection on which it received that Control Packet which caused the protocol violation.
```

Therefore, receiving type `15` in an MQTT 3.1.1 session is a protocol violation and must not be accepted successfully.

## Code

`implementions/wolfMQTT-master/wolfmqtt/mqtt_packet.h:278-279` defines packet type `15` as MQTT 5 AUTH:

```c
MQTT_PACKET_TYPE_DISCONNECT = 14,
MQTT_PACKET_TYPE_AUTH = 15,             /* Authentication (MQTT 5) */
```

`implementions/wolfMQTT-master/src/mqtt_packet.c:187-204` accepts fixed-header flags `0x0` for AUTH, so `F0` passes fixed-header flag validation:

```c
case MQTT_PACKET_TYPE_DISCONNECT:
case MQTT_PACKET_TYPE_AUTH:
    *expected = 0x0;
    return 1;
```

That is acceptable for MQTT 5 support by itself. The issue is the client decode branch.

`implementions/wolfMQTT-master/src/mqtt_client.c:1047-1069` checks only the compile-time `WOLFMQTT_V5` flag and not the current `client->protocol_level`:

```c
case MQTT_PACKET_TYPE_AUTH:
{
#ifdef WOLFMQTT_V5
    MqttAuth auth, *p_auth = &auth;
    ...
    rc = MqttDecode_Auth(rx_buf, rx_len, p_auth);
#else
    rc = MQTT_TRACE_ERROR(MQTT_CODE_ERROR_PACKET_TYPE);
#endif
    break;
}
```

`implementions/wolfMQTT-master/src/mqtt_packet.c:4132-4214` decodes an empty AUTH packet as MQTT 5 success:

```c
else {
    auth->reason_code = MQTT_REASON_SUCCESS;
}
return header_len + remain_len;
```

After `MqttDecode_Auth(F0 00)` returns success in a v5-enabled build, the MQTT 3.1.1 client remains connected because `src/mqtt_client.c:2032-2074` clears the connected flag only on negative fatal protocol errors.

Control paths:

- A non-v5 build returns `MQTT_CODE_ERROR_PACKET_TYPE` in the same branch.
- Outbound `MqttClient_Auth` requires a negotiated Authentication Method and is not this issue.
- Broker dispatch closes unhandled type `15` and is not this issue.

## Runtime Evidence

A focused reproducer was compiled twice: once without `WOLFMQTT_V5` as a negative control, and once with `WOLFMQTT_V5` while keeping `protocol_level=4`.

Both builds completed successfully. The non-v5 control produced:

```text
WOLFMQTT_V5=no protocol_level=4 input=F0 00 rc=-4 connected=0 disconnect_calls=0 canned_pos=2
direct MqttDecode_Auth unavailable without WOLFMQTT_V5
```

The v5-enabled MQTT 3.1.1 reproducer produced:

```text
WOLFMQTT_V5=yes protocol_level=4 input=F0 00 rc=0 connected=1 disconnect_calls=0 canned_pos=2
direct MqttDecode_Auth(F0 00) rc=2 reason=0 props=[hex omitted]
```

Observed result: without v5 support the packet is rejected as packet type error (`-4`); with v5 support enabled but MQTT 3.1.1 protocol level still selected, the same `F0 00` packet is accepted (`rc=0`) and the client remains connected.

## Why This Is An Issue

A v5-enabled wolfMQTT client can operate as MQTT 3.1.1 (`protocol_level=4`), but inbound AUTH handling is gated only by compile-time support. It does not reject type `15` based on the runtime protocol version, so an MQTT 3.1.1 client accepts a Control Packet type that the standard marks `Forbidden` and does not close the connection as required.

## Fix Direction

Add a runtime protocol-version check before inbound AUTH decode, for example in the `MQTT_PACKET_TYPE_AUTH` branch of `MqttClient_DecodePacket`:

```c
if (client->protocol_level < MQTT_CONNECT_PROTOCOL_LEVEL_5) {
    rc = MQTT_TRACE_ERROR(MQTT_CODE_ERROR_PACKET_TYPE);
    break;
}
```

If `MqttPacket_FixedHeaderFlagsValid` remains version-independent, ensure all MQTT 3.1.1 receive paths reject type `15` at dispatch or decode time.
