# Client transient error does not close the network connection

## Summary

The client can return `MQTT_CODE_ERROR_OUT_OF_BUFFER` while decoding an inbound `PUBLISH`, but it does not call the configured network `disconnect` callback and still reports the connection flag as set.

## Standard Requirement

Official standard: [MQTT Version 3.1.1](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html)

MQTT 3.1.1 Section 4.8, Handling errors:

```text
If the Client or Server encounters a Transient Error while processing an inbound Control Packet it MUST close the Network Connection
```

For this report, the relevant endpoint is the client. If the client encounters a transient error while processing an inbound Control Packet, such as an internal buffer-full condition, it must close the connection that delivered that packet.

## Relevant Source Code

`implementions/wolfMQTT-master/src/mqtt_packet.c:769-779` returns `MQTT_CODE_ERROR_OUT_OF_BUFFER` when a decoded MQTT string length exceeds the available buffer:

```c
int MqttDecode_String(byte *buf, const char **pstr, word16 *pstr_len,
    word32 buf_len)
{
    int len;
    word16 str_len;
    len = MqttDecode_Num(buf, &str_len, buf_len);
    if (len < 0) {
        return len;
    }
    if ((word32)str_len > buf_len - (word32)len) {
        return MQTT_TRACE_ERROR(MQTT_CODE_ERROR_OUT_OF_BUFFER);
    }
```

The `PUBLISH` Topic Name is an MQTT string, so this error can occur while decoding an inbound `PUBLISH` Control Packet.

`implementions/wolfMQTT-master/src/mqtt_client.c:1628-1656` reads and decodes inbound packets:

```c
rc = MqttPacket_Read(client, client->rx_buf, client->rx_buf_len,
        timeout_ms);
if (rc <= 0) {
    break;
}

rc = MqttClient_DecodePacket(client, client->rx_buf,
    client->packet.buf_len, NULL, &packet_type, &packet_qos,
    &packet_id, 1);
if (rc < 0) {
    break;
}
```

`implementions/wolfMQTT-master/src/mqtt_client.c:1403-1412` defines the client fatal protocol error list:

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

`MQTT_CODE_ERROR_OUT_OF_BUFFER` is not included.

`implementions/wolfMQTT-master/src/mqtt_client.c:2056-2074` handles negative returns by clearing the connection flag only for selected fatal protocol errors, and does not call the network disconnect path:

```c
if (rc < 0) {
    if (recvFatal && MqttClient_IsFatalProtoError(rc) &&
        (client->flags & MQTT_CLIENT_FLAG_IS_CONNECTED) != 0) {
        (void)MqttClient_Flags(client, MQTT_CLIENT_FLAG_IS_CONNECTED, 0);
    }
    return rc;
}
```

`implementions/wolfMQTT-master/src/mqtt_socket.c:554-584` contains the actual network disconnect path:

```c
int MqttSocket_Disconnect(MqttClient *client)
{
    int rc = MQTT_CODE_SUCCESS;
    if (client) {
        if (client->net && client->net->disconnect) {
            rc = client->net->disconnect(client->net->context);
        }
        MqttClient_Flags(client, MQTT_CLIENT_FLAG_IS_CONNECTED, 0);
```

That path exists, but the transient inbound error path above does not invoke it.

## Implementation Behavior

The probe constructed a QoS 0 `PUBLISH` with Remaining Length `0x16` and a Topic Name length field of `0x0014`, while giving the client an `rx_buf` of only 16 bytes. `MqttPacket_Read` read the inbound packet prefix and entered decode; `MqttDecode_String` returned `MQTT_CODE_ERROR_OUT_OF_BUFFER`.

`MqttClient_WaitMessage` then returned the error. Because this return code is not in `MqttClient_IsFatalProtoError`, wolfMQTT neither called `net->disconnect` nor cleared `MQTT_CLIENT_FLAG_IS_CONNECTED`.

## Inconsistency Reason

The standard requires the client to close the network connection when it encounters a transient error while processing an inbound Control Packet. wolfMQTT instead reports `MQTT_CODE_ERROR_OUT_OF_BUFFER`, leaves the configured disconnect callback uncalled, and keeps the client marked as connected.

## Runtime Evidence

A focused transient-error probe was compiled and run. It used a small client receive buffer and an inbound QoS 0 `PUBLISH` whose Topic Name length exceeded the available buffer, then counted network `disconnect` calls and inspected the connected flag after `MqttClient_WaitMessage` returned.

```text
build_exit_code=0
rc_init=0 rc_net=0 rc_wait=-2
MQTT_CODE_ERROR_OUT_OF_BUFFER=-2 MQTT_CLIENT_FLAG_IS_CONNECTED=0x01
connect_count=1 disconnect_count=0 read_count=2 script_pos=16
flags_after=0x00000001 connected_after=1
RESULT=reproduced_transient_error_did_not_close_connection
run_exit_code=1
```

The key observation is `disconnect_count=0` together with `connected_after=1` after the inbound transient error. The probe uses `run_exit_code=1` as the expected marker for a reproduced issue, not as a crash indicator.

## Impact

After buffer exhaustion or a similar transient error during inbound packet processing, the underlying connection can remain open even though MQTT 3.1.1 requires it to be closed.

## Fix Direction

In the inbound processing error path of `MqttClient_WaitMessage` and `MqttClient_WaitType`, treat `MQTT_CODE_ERROR_OUT_OF_BUFFER` and other transient processing errors as connection-closing conditions. The implementation should call `MqttClient_NetDisconnect` or an equivalent socket-close path so the configured network callback runs and the connected flag is cleared.
