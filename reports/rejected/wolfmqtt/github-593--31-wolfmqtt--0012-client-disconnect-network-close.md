# Client DISCONNECT does not close the network connection

## Summary

`MqttClient_Disconnect_ex` writes an MQTT `DISCONNECT` packet, but it does not close the transport or clear the connected flag. A runtime probe confirmed that immediately after sending `DISCONNECT`, the network disconnect callback has not run and another control packet can still be written.

## Standard Requirement

Official standard: [MQTT Version 3.1.1](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html), Section 3.14.4 "Response"

```text
[MQTT-3.14.4-1] After sending a DISCONNECT Packet the Client MUST close the Network Connection.
[MQTT-3.14.4-2] After sending a DISCONNECT Packet the Client MUST NOT send any more Control Packets on that Network Connection.
```

The client must close the network connection after sending `DISCONNECT`; it must not leave the connection usable for later MQTT Control Packets.

## Relevant Source Code

`implementions/wolfMQTT-master/src/mqtt_client.c:3692-3801`

```c
int MqttClient_Disconnect(MqttClient *client)
{
    return MqttClient_Disconnect_ex(client, NULL);
}

int MqttClient_Disconnect_ex(MqttClient *client, MqttDisconnect *p_disconnect)
{
    ...
    rc = MqttEncode_Disconnect(client->tx_buf, client->tx_buf_len,
        disconnect);
    ...
    rc = MqttPacket_Write(client, client->tx_buf, xfer);
    ...
    if (rc == xfer) {
        rc = MQTT_CODE_SUCCESS;
    }
    ...
    return rc;
}
```

This function encodes and writes `DISCONNECT`, then returns. It does not call the transport close path.

`implementions/wolfMQTT-master/src/mqtt_client.c:4271-4322`

```c
int MqttClient_NetDisconnect(MqttClient *client)
{
    ...
    return MqttSocket_Disconnect(client);
}
```

`implementions/wolfMQTT-master/src/mqtt_socket.c:554-584`

```c
int MqttSocket_Disconnect(MqttClient *client)
{
    ...
    if (client->net && client->net->disconnect) {
        rc = client->net->disconnect(client->net->context);
    }
    MqttClient_Flags(client, MQTT_CLIENT_FLAG_IS_CONNECTED, 0);
```

Only `MqttClient_NetDisconnect` reaches `MqttSocket_Disconnect`, which calls the configured network disconnect callback and clears `MQTT_CLIENT_FLAG_IS_CONNECTED`.

`implementions/wolfMQTT-master/examples/pub-sub/mqtt-pub.c:435-448`

```c
rc = MqttClient_Disconnect_ex(&mqttCtx->client, &mqttCtx->disconnect);
...
rc = MqttClient_NetDisconnect(&mqttCtx->client);
```

The example also treats MQTT `DISCONNECT` and network close as two separate steps.

## Implementation Behavior

After `MqttClient_Disconnect_ex` succeeds, the MQTT `DISCONNECT` bytes have been written, but the network callback has not been called and the connected flag remains set. The application must call `MqttClient_NetDisconnect` separately to actually close the transport.

## Inconsistency Reason

MQTT-3.14.4-1 requires the client to close the Network Connection after sending `DISCONNECT`, and MQTT-3.14.4-2 forbids any later Control Packet on that connection. wolfMQTT splits this into two APIs: `MqttClient_Disconnect_ex` sends `DISCONNECT`, while `MqttClient_NetDisconnect` closes the transport. Because the send API returns success without closing or marking the connection closed, the protocol-required state transition is not completed.

## Runtime Evidence

A focused client probe was compiled and run. It initialized a connected client with a network `disconnect` counter, called `MqttClient_Disconnect`, immediately inspected the counter and connected flag, and then attempted another MQTT Control Packet on the same connection.

Observed result:

```text
compile_exit_code=0
init_rc=0 net_rc=0 mqtt_disconnect_rc=0 ping_after_mqtt_disconnect_rc=-7 net_disconnect_rc=0 connect_calls=1 write_calls=2 disconnects_immediately_after_mqtt_disconnect=0 disconnects_after_ping=1 final_disconnect_calls=2 connected_after_mqtt_disconnect=1 first_write=E0 00 second_write=C0 00
exit_code=2
```

The probe first calls `MqttClient_Disconnect`. It writes `E0 00` (`DISCONNECT`) and returns success, but `disconnects_immediately_after_mqtt_disconnect=0` and `connected_after_mqtt_disconnect=1`. It then calls `MqttClient_Ping`, which writes `C0 00` (`PINGREQ`) on the same connection before the read timeout path closes it.

## Impact

The client API can leave the connection open and usable after a successful MQTT `DISCONNECT`, allowing later Control Packets on a connection that the standard says must already be closed.

## Fix Direction

After `MqttClient_Disconnect_ex` successfully writes `DISCONNECT`, call the same transport close path used by `MqttClient_NetDisconnect`, or at least clear the connected state and prevent any further MQTT Control Packet writes on that connection.
