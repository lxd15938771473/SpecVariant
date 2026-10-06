# MQTT 3.1.1 client fatal input does not close the network connection

## Summary

MQTT 3.1.1 requires the receiver to close the Network Connection after malformed UTF-8, UTF-8 strings containing U+0000, invalid fixed-header flags, and other protocol-violating Control Packets. wolfMQTT client recognizes these errors and clears `IS_CONNECTED`, but it does not call the network-layer `disconnect` callback. Runtime evidence shows that the underlying disconnect callback is not invoked.

## Standard Requirement

Reference: [MQTT v3.1.1 specification, Sections 1.5.3, 2.2.2, and 4.8](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html).

```text
If a Server or Client receives a Control Packet containing ill-formed UTF-8 it MUST close the Network Connection [MQTT-1.5.3-1].
```

```text
If a receiver (Server or Client) receives a Control Packet containing U+0000 it MUST close the Network Connection [MQTT-1.5.3-2].
```

```text
If invalid flags are received, the receiver MUST close the Network Connection [MQTT-2.2.2-2].
```

```text
Unless stated otherwise, if either the Server or Client encounters a protocol violation, it MUST close the Network Connection on which it received that Control Packet which caused the protocol violation [MQTT-4.8.0-1].
```

These clauses require closing the Network Connection that carried the violating packet, not merely marking an internal library flag as disconnected.

## Relevant Source Code

`implementions/wolfMQTT-master/src/mqtt_packet.c:769-793` rejects malformed UTF-8 and U+0000 while decoding UTF-8 strings:

```c
if (!Utf8WellFormed(buf, str_len)) {
    return MQTT_TRACE_ERROR(MQTT_CODE_ERROR_MALFORMED_DATA);
}
if (XMEMCHR(buf, 0x00, str_len) != NULL) {
    return MQTT_TRACE_ERROR(MQTT_CODE_ERROR_MALFORMED_DATA);
}
```

`implementions/wolfMQTT-master/src/mqtt_packet.c:507-555` rejects invalid fixed-header flags:

```c
if (!MqttPacket_FixedHeaderFlagsValid(header->type_flags)) {
    return MQTT_TRACE_ERROR(MQTT_CODE_ERROR_MALFORMED_DATA);
}
```

`implementions/wolfMQTT-master/src/mqtt_client.c:1403-1411` classifies these return codes as fatal protocol errors:

```c
static int MqttClient_IsFatalProtoError(int rc)
{
    return (rc == MQTT_CODE_ERROR_MALFORMED_DATA ||
            rc == MQTT_CODE_ERROR_PACKET_TYPE ||
            rc == MQTT_CODE_ERROR_PACKET_ID ||
            rc == MQTT_CODE_ERROR_PROPERTY ||
            rc == MQTT_CODE_ERROR_PROPERTY_MISMATCH ||
            rc == MQTT_CODE_ERROR_SERVER_PROP);
}
```

`implementions/wolfMQTT-master/src/mqtt_client.c:2032-2074` then clears only the connected flag and explicitly leaves transport teardown to the application:

```c
/* Only clear the flag; the application tears the
 * transport down via MqttClient_NetDisconnect. */
if (recvFatal && MqttClient_IsFatalProtoError(rc) &&
    (client->flags & MQTT_CLIENT_FLAG_IS_CONNECTED) != 0) {
    (void)MqttClient_Flags(client, MQTT_CLIENT_FLAG_IS_CONNECTED, 0);
}
return rc;
```

The path that actually calls `net->disconnect` is explicit `MqttClient_NetDisconnect` through `MqttSocket_Disconnect`.

## Implementation Behavior

When the client receives one of these violating packets, the decoder returns `MQTT_CODE_ERROR_MALFORMED_DATA`, `MqttClient_WaitType` treats it as fatal received-data error, and cleanup clears `MQTT_CLIENT_FLAG_IS_CONNECTED`. Cleanup does not call `MqttClient_NetDisconnect` or `MqttSocket_Disconnect`, so `net->disconnect` is not invoked.

This report is about the wolfMQTT client receiving invalid data from a server. The broker path is separate and has abnormal-close logic.

## Inconsistency Reason

The standard requires the receiver to close the Network Connection that delivered the violating Control Packet. wolfMQTT client marks its internal state as disconnected and requires the application to perform the actual network disconnect later. That does not satisfy the mandatory close requirement because the underlying `net->disconnect` callback has not run.

## Runtime Evidence

A focused client receive-path probe was compiled and run. The positive control explicitly called `MqttClient_NetDisconnect` to prove the mock network disconnect counter works:

```text
explicit NetDisconnect invoked disconnect callback once
```

Then three violating inputs were delivered to the client receive path:

```text
malformed UTF-8 PUBLISH topic observed rc=-3 disconnect_calls=0 connected_flag=0
NUL UTF-8 PUBLISH topic observed rc=-3 disconnect_calls=0 connected_flag=0
invalid PUBACK flags observed rc=-3 disconnect_calls=0 connected_flag=0
```

Observed result: each malformed input was recognized as malformed data (`rc=-3`) and the internal connected flag was cleared, but `disconnect_calls` remained `0`. The positive control shows that the probe would have observed the disconnect callback if the library had invoked it.

## Impact

If an application does not call `MqttClient_NetDisconnect` after every fatal receive error, the underlying TCP/TLS transport can remain open even though MQTT 3.1.1 requires it to be closed.

## Fix Direction

Actively close the transport in the client receive path when a fatal protocol error is detected. The cleanup path can call `MqttClient_NetDisconnect` or an equivalent internal disconnect routine, while avoiding double teardown with explicit disconnect, curl, or multithread cleanup paths.
