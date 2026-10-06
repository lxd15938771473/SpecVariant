# CleanSession=0 reconnect does not retransmit an unacknowledged PUBREL

## Summary

MQTT 3.1.1 requires a client that reconnects with `CleanSession=0` to resend any unacknowledged `PUBREL` packet using the original Packet Identifier. In wolfMQTT, after a QoS 2 `PUBREL` has been written but the corresponding `PUBCOMP` has not been received, disconnecting and reconnecting with `clean_session=0` sends only a new `CONNECT`. The original `PUBREL` is not retransmitted. Broker-side persistent-session behavior does not prove that the client satisfies this requirement.

## Standard Requirement

Official standard: [MQTT Version 3.1.1](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html)

MQTT 3.1.1 Section 4.4, Message delivery retry:

```text
When a Client reconnects with CleanSession set to 0, both the Client and Server MUST re-send any unacknowledged PUBLISH Packets (where QoS > 0) and PUBREL Packets using their original Packet Identifiers [MQTT-4.4.0-1].
```

This means that when a client reconnects with a persistent session and still has a QoS 2 `PUBREL` for which `PUBCOMP` has not arrived, the client must retransmit that `PUBREL` with the same Packet Identifier.

## Relevant Source Code

`implementions/wolfMQTT-master/wolfmqtt/mqtt_client.h:429` documents the QoS 2 publish flow as sending `PUBREL` and waiting for `PUBCOMP`:

```c
If QoS level = 2 then will wait for PUBLISH_REC then send
    PUBLISH_REL and wait for PUBLISH_COMP.
```

`implementions/wolfMQTT-master/src/mqtt_client.c:3195` waits for `PUBCOMP` in the QoS 2 publish path:

```c
rc = MqttClient_WaitType(client, &publish->resp, resp_type,
    publish->packet_id, client->cmd_timeout_ms, NULL);
```

`implementions/wolfMQTT-master/src/mqtt_client.c:1964` can encode and write `PUBREL` after receiving `PUBREC`:

```c
rc = MqttEncode_PublishResp(client->tx_buf, client->tx_buf_len,
    client->packetAck.packet_type, &client->packetAck);
...
rc = MqttPacket_Write(client, client->tx_buf, xfer);
```

`implementions/wolfMQTT-master/src/mqtt_packet.c:2686` and `:2701` preserve the Packet Identifier when encoding `PUBREL`:

```c
qos = (type == MQTT_PACKET_TYPE_PUBLISH_REL) ? MQTT_QOS_1 : MQTT_QOS_0;
...
tx_payload += MqttEncode_Num(&tx_buf[header_len], publish_resp->packet_id);
```

`implementions/wolfMQTT-master/src/mqtt_client.c:2564` and `:2606` show that `MqttClient_Connect` waits for `CONNACK` and resets only the CONNECT state. It does not replay unacknowledged `PUBREL` packets:

```c
rc = MqttClient_WaitType(client, &mc_connect->ack,
    MQTT_PACKET_TYPE_CONNECT_ACK, 0, client->cmd_timeout_ms, NULL);
...
mc_connect->stat.write = MQTT_MSG_BEGIN;
```

`implementions/wolfMQTT-master/src/mqtt_client.c:4271` closes the network connection but does not save or replay an outgoing `PUBREL` queue:

```c
int MqttClient_NetDisconnect(MqttClient *client)
{
    ...
    return MqttSocket_Disconnect(client);
}
```

## Implementation Behavior

The client can send `PUBREL` during a single QoS 2 publish exchange and then wait for `PUBCOMP`. However, if the connection is closed after `PUBREL` has been sent and before `PUBCOMP` arrives, `MqttClient_NetDisconnect` does not preserve an outgoing `PUBREL` state that can be scanned after reconnect. After `MqttClient_Connect(clean_session=0)` succeeds, the connect path also has no retransmission logic for that unacknowledged `PUBREL`.

## Inconsistency Reason

The standard requires a client reconnecting with `CleanSession=0` to retransmit every unacknowledged `PUBREL` with the original Packet Identifier. wolfMQTT keeps only the in-progress publish state machine state and does not automatically retransmit the already-written `PUBREL` after reconnect. If the disconnect occurs after `PUBREL` and before `PUBCOMP`, the broker may still be waiting for the original `PUBREL`, while the client resumes without restoring that QoS 2 exchange.

## Runtime Evidence

A focused nonblocking client probe was compiled and run. It completed the first CONNECT, sent a QoS 2 PUBLISH, fed a broker `PUBREC` so that wolfMQTT wrote `PUBREL(packet_id=7)`, withheld `PUBCOMP`, disconnected the network, and then reconnected with `clean_session=0`. The probe recorded every client write after the reconnect.

```text
rc_conn1=0 rc_pub1=-101 rc_disc=0 rc_conn2=0 rc_pub2=-101
MQTT_CODE_SUCCESS=0 MQTT_CODE_CONTINUE=-101
writes_after_pubrel=3 write_count=4 read_count=8 disconnect_count=1
write[0]_first=0x10
write[1]_first=0x34
write[2]_first=0x62 pubrel_packet_id=7
write[3]_first=0x10
pubrel_after_reconnect=0
RESULT=reproduced_no_pubrel_retransmit_after_reconnect
run_exit_code=1
```

`write[2]` is the original `PUBREL` with Packet Identifier `7`. `write[3]` is the reconnect `CONNECT`. After reconnect, `pubrel_after_reconnect=0`, so no `PUBREL` was retransmitted. The probe uses `run_exit_code=1` as the expected marker for a reproduced issue, not as a crash indicator.

## Impact

A QoS 2 publish can become stuck across reconnect at the `PUBREL`/`PUBCOMP` stage. The broker-side persistent session may wait for a `PUBREL` that the client no longer retransmits, so message-delivery recovery no longer matches MQTT 3.1.1.

## Fix Direction

For `CleanSession=0` client sessions, preserve unacknowledged outgoing QoS > 0 `PUBLISH` packets and `PUBREL` packets. After a successful reconnect and `CONNACK`, retransmit the saved packets with their original Packet Identifiers, and remove them only after the corresponding `PUBACK` or `PUBCOMP` is processed or the persistent session is intentionally cleared.
