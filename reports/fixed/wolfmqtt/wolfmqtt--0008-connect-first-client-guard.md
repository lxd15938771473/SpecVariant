# Client can send PUBLISH before CONNECT

## Summary

The confirmed scope is the wolfMQTT client API. After an application calls `MqttClient_NetConnect` to establish the transport connection, it can call `MqttClient_Publish` before `MqttClient_Connect`; wolfMQTT then writes `PUBLISH` as the first MQTT packet from the client to the server.

This is not the case where the client sends additional Control Packets after sending CONNECT but before CONNACK; MQTT 3.1.1 allows that. The issue occurs before CONNECT has been sent. The broker/server path already has CONNECT-first checks and is not the confirmed gap.

## Standard Requirement

Reference: [MQTT v3.1.1 specification, Sections 3.1 and 3.1.4](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html).

```text
After a Network Connection is established by a Client to a Server, the first Packet sent from the Client to the Server MUST be a CONNECT Packet [MQTT-3.1.0-1].
```

```text
Clients are allowed to send further Control Packets immediately after sending a CONNECT Packet; Clients need not wait for a CONNACK Packet to arrive from the Server.
```

Therefore, the client may avoid waiting for CONNACK after CONNECT is sent, but it must not send `PUBLISH`, `SUBSCRIBE`, `PINGREQ`, or other MQTT Control Packets before CONNECT.

## Relevant Source Code

`implementions/wolfMQTT-master/src/mqtt_socket.c:400-411` sets `MQTT_CLIENT_FLAG_IS_CONNECTED` after the transport connection succeeds:

```c
if ((MqttClient_Flags(client, 0, 0) & MQTT_CLIENT_FLAG_IS_CONNECTED) == 0) {
    rc = client->net->connect(client->net->context, host, port, timeout_ms);
    if (rc != MQTT_CODE_SUCCESS) {
        return rc;
    }
    MqttClient_Flags(client, 0, MQTT_CLIENT_FLAG_IS_CONNECTED);
}
```

`MqttClient_NetConnect` is only a wrapper for that transport connection.

`MqttClient_Publish` encodes and writes `PUBLISH` without checking whether MQTT CONNECT has already been sent:

```c
rc = MqttEncode_Publish(client->tx_buf, client->tx_buf_len,
        publish, pubCb ? 1 : 0);

int MqttClient_Publish(MqttClient *client, MqttPublish *publish)
{
    return MqttPublishMsg(client, publish, NULL, 0);
}
```

The write path proceeds through `MqttPacket_Write` and `MqttSocket_Write`, which check arguments and call `net->write`; they do not enforce CONNECT-first ordering.

The broker path already rejects non-CONNECT first packets and second CONNECT packets, so this report is limited to client packet generation.

## Implementation Behavior

`MQTT_CLIENT_FLAG_IS_CONNECTED` means only that the transport exists. It does not mean MQTT CONNECT has been sent or accepted. Because `MqttClient_Publish` lacks a separate MQTT handshake-state check, an application that calls `MqttClient_NetConnect` then `MqttClient_Publish` writes `PUBLISH` as the first MQTT packet on that connection.

## Inconsistency Reason

The standard requires the first client-to-server packet after Network Connection establishment to be CONNECT. wolfMQTT exposes transport-connected state to later sending APIs without tracking whether CONNECT has been sent as the first MQTT packet, so the client API can generate MQTT-3.1.0-1-invalid ordering.

## Runtime Evidence

A focused packet probe was compiled and run. The positive control called `MqttClient_NetConnect` followed by `MqttClient_Connect`; the first MQTT packet was CONNECT:

```text
NetConnect followed by MqttClient_Connect wrote CONNECT as first packet
```

The reproducer called `MqttClient_NetConnect` followed directly by `MqttClient_Publish`; the first MQTT packet was PUBLISH:

```text
NetConnect followed by Publish wrote packet type 3 before CONNECT
```

Observed result: the positive control produced first byte `0x10` for CONNECT, while the reproducer produced a packet whose high nibble was `0x30`, i.e. PUBLISH type 3.

## Impact

Applications that mistakenly call wolfMQTT as `NetConnect -> Publish -> Connect` will cause the library to write an invalid first packet. A conforming broker should close the connection, causing connection failure or unusable state.

## Fix Direction

Add independent MQTT connection-stage state such as `CONNECT_NOT_SENT`, `CONNECT_SENT`, and `CONNACK_ACCEPTED`. Before `CONNECT_SENT`, sending APIs other than `MqttClient_Connect` should refuse to write MQTT Control Packets. After CONNECT is sent, the client may allow the standard-permitted early packets without waiting for CONNACK.
