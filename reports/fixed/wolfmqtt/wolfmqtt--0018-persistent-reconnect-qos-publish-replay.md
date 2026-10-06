# wolfMQTT client does not replay unacknowledged QoS PUBLISH after persistent-session reconnect

## Summary

MQTT 3.1.1 requires a client reconnecting with `CleanSession=0` to retransmit unacknowledged QoS > 0 `PUBLISH` packets with their original Packet Identifiers. wolfMQTT's client reconnect path sends `CONNECT` and waits for `CONNACK`, but it does not preserve and automatically replay previously unacknowledged outbound `PUBLISH` packets. Runtime evidence confirms the missing replay.

## Standard Requirement

Official standard: [MQTT Version 3.1.1](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html)

MQTT 3.1.1 Section 4.4, Message delivery retry:

```text
[MQTT-4.4.0-1] When a Client reconnects with CleanSession set to 0, both the Client and Server MUST re-send any unacknowledged PUBLISH Packets (where QoS > 0) and PUBREL Packets using their original Packet Identifiers.
```

The related DUP rule is in MQTT 3.1.1 Section 3.3.1.1:

```text
[MQTT-3.3.1-1] The DUP flag MUST be set to 1 by the Client or Server when it attempts to re-deliver a PUBLISH Packet.
```

Therefore, if a client has an unacknowledged QoS 1 or QoS 2 `PUBLISH`, after a successful `CleanSession=0` reconnect it must automatically retransmit that packet, preserve the Packet Identifier, and set `DUP=1` on the retransmitted `PUBLISH`.

## Relevant Source Code

`implementions/wolfMQTT-master/src/mqtt_client.c:2291` is the client `CONNECT` entry point. It encodes and writes `CONNECT`, then waits for `CONNACK`:

```c
int MqttClient_Connect(MqttClient *client, MqttConnect *mc_connect)
{
    ...
    rc = MqttEncode_Connect(client->tx_buf, client->tx_buf_len, mc_connect);
    ...
    rc = MqttPacket_Write(client, client->tx_buf, xfer);
    ...
    rc = MqttClient_WaitType(client, &mc_connect->ack,
        MQTT_PACKET_TYPE_CONNECT_ACK, 0, client->cmd_timeout_ms, NULL);
```

After `CONNACK`, this function does not scan or replay saved unacknowledged outbound `PUBLISH` or `PUBREL` packets.

`implementions/wolfMQTT-master/src/mqtt_client.c:3272` resets the caller's publish object state after a publish attempt ends:

```c
if ((rc != MQTT_CODE_PUB_CONTINUE)
#ifdef WOLFMQTT_NONBLOCK
     && (rc != MQTT_CODE_CONTINUE)
#endif
    )
{
    publish->stat.write = MQTT_MSG_BEGIN;
}
```

`implementions/wolfMQTT-master/src/mqtt_packet.c:2401` encodes `PUBLISH` using the caller-provided `duplicate` flag:

```c
header_len = MqttEncode_FixedHeader(tx_buf, tx_buf_len,
    variable_len + payload_len, publish->type,
    publish->retain, publish->qos, publish->duplicate);
```

The broker side has independent replay logic, so this report does not attribute the issue to the broker. When the broker reclaims an orphaned session, it marks in-flight messages for retransmission:

```c
/* implementions/wolfMQTT-master/src/mqtt_broker.c:3612 */
if (e->state == BROKER_OUTQ_PUBLISH_SENT) {
    e->state = BROKER_OUTQ_QUEUED;
    e->retransmit_dup = 1;
}

/* implementions/wolfMQTT-master/src/mqtt_broker.c:2057 */
out_pub.packet_id  = cur->packet_id;
out_pub.duplicate  = cur->retransmit_dup;
```

## Implementation Behavior

wolfMQTT provides a publish API, but the client does not maintain an outbound session queue that survives disconnect and reconnect. After `MqttClient_Connect(... clean_session=0 ...)` succeeds, the library does not automatically send the QoS > 0 `PUBLISH` packets that were still unacknowledged before the disconnect.

An application can manually call `MqttClient_Publish` again, but that is application-driven retry rather than reconnect replay. The DUP bit also remains controlled by the caller, so a manual retry does not demonstrate compliance with MQTT 3.1.1's required automatic retransmission semantics.

## Inconsistency Reason

The standard requires the client to resend unacknowledged QoS > 0 `PUBLISH` packets when reconnecting with `CleanSession=0`. wolfMQTT's client reconnect path only completes the CONNECT handshake; it has no path that persists unacknowledged publications and replays them after reconnect. The required retransmission can therefore be missed.

## Runtime Evidence

A focused client probe was compiled and run. It connected with a persistent session, sent a QoS 1 `PUBLISH(packet_id=0x1234)`, withheld `PUBACK` until the publish call timed out, disconnected, and then reconnected with `CleanSession=0`. The probe recorded all packets written by the client before and after reconnect. It then performed a manual retry to distinguish application-level retry from automatic reconnect replay.

```text
rc_connect1=0 rc_publish1=-7 rc_disconnect=0 rc_connect2=0
connects=2 disconnects=1 reads=5 writes_after_reconnect=3
write[1]=CONNECT first=0x10 dup=0 packet_id=0x0000
write[2]=PUBLISH first=0x32 dup=0 packet_id=0x1234
write[3]=CONNECT first=0x10 dup=0 packet_id=0x0000
rc_retry=-7 writes_after_manual_retry=4
write[4]=PUBLISH first=0x32 dup=0 packet_id=0x1234
```

The first `CONNECT` succeeds. The QoS 1 `PUBLISH` with Packet Identifier `0x1234` is sent and then times out while waiting for `PUBACK`, so it remains unacknowledged. The second `CONNECT` also succeeds with `CleanSession=0`. `writes_after_reconnect=3` means that after reconnect the only new write is the second `CONNECT`; no automatic `PUBLISH` retransmission occurs. `write[4]` appears only after a manual retry, and `first=0x32` shows `DUP=0`, not the required `DUP=1` retransmission form.

## Impact

Applications that rely on wolfMQTT client persistent-session behavior can lose required QoS 1 or QoS 2 outbound retransmissions after reconnect, weakening MQTT's delivery guarantees.

## Fix Direction

Add a persistent outbound client session queue for QoS 1/QoS 2 `PUBLISH` packets and QoS 2 `PUBREL` packets. After a successful `CleanSession=0` reconnect, replay unacknowledged entries with their original Packet Identifiers. When replaying `PUBLISH`, set `DUP=1`.
