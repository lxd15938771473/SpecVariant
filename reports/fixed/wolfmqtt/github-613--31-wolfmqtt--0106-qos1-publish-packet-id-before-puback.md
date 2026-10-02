# QoS 1 PUBLISH reuses Packet Identifier before PUBACK

## Summary

Extracted rule: while a QoS 1 `PUBLISH` is waiting for the corresponding `PUBACK`, its Packet Identifier must remain unavailable for reuse.

wolfMQTT can send a second new QoS 1 `PUBLISH` with the same Packet Identifier before the first `PUBACK` has been processed.

## Standard Requirement

Official standard: [MQTT Version 3.1.1](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html)

MQTT 3.1.1 Section 2.3.1, Packet Identifier, with the QoS 1 flow defined in Section 4.3.2:

```text
The Packet Identifier becomes available for reuse after the Client has processed the corresponding acknowledgement packet.
```

For QoS 1 `PUBLISH`, the corresponding acknowledgement packet is `PUBACK`. A new application message cannot reuse the Packet Identifier before that `PUBACK` is processed. Reusing the original identifier is valid only when retransmitting the same Control Packet.

## Relevant Source Code

`MqttEncode_Publish` verifies only that QoS > 0 publishes use a nonzero Packet Identifier, then directly encodes the caller-provided identifier:

```c
/* implementions/wolfMQTT-master/src/mqtt_packet.c:2350-2354 */
if (publish->qos > MQTT_QOS_0) {
    if (publish->packet_id == 0) {
        return MQTT_TRACE_ERROR(MQTT_CODE_ERROR_PACKET_ID);
    }
    variable_len += MQTT_DATA_LEN_SIZE; /* For packet_id */
}

/* implementions/wolfMQTT-master/src/mqtt_packet.c:2420-2421 */
if (publish->qos > MQTT_QOS_0) {
    tx_payload += MqttEncode_Num(tx_payload, publish->packet_id);
}
```

`MqttPublishMsg` adds pending-response and duplicate-id protection only under `WOLFMQTT_MULTITHREAD`, which is not enabled by default:

```c
/* implementions/wolfMQTT-master/src/mqtt_client.c:3060-3070 */
#ifdef WOLFMQTT_MULTITHREAD
if (publish->qos > MQTT_QOS_0) {
    resp_type = (publish->qos == MQTT_QOS_1) ?
            MQTT_PACKET_TYPE_PUBLISH_ACK :
            MQTT_PACKET_TYPE_PUBLISH_COMP;
    rc = MqttClient_RespList_Add(client, resp_type,
        publish->packet_id, &publish->pendResp, &publish->resp);
}
#endif
```

The send-and-wait path writes the `PUBLISH`, waits for `PUBACK`, and then resets `publish->stat.write` when the wait ends:

```c
/* implementions/wolfMQTT-master/src/mqtt_client.c:3100 */
rc = MqttPacket_Write(client, client->tx_buf, xfer);

/* implementions/wolfMQTT-master/src/mqtt_client.c:3195-3196 */
rc = MqttClient_WaitType(client, &publish->resp, resp_type,
    publish->packet_id, client->cmd_timeout_ms, NULL);

/* implementions/wolfMQTT-master/src/mqtt_client.c:3265-3273 */
if ((rc != MQTT_CODE_PUB_CONTINUE)
#ifdef WOLFMQTT_NONBLOCK
     && (rc != MQTT_CODE_CONTINUE)
#endif
    )
{
    publish->stat.write = MQTT_MSG_BEGIN;
}
```

`CMakeLists.txt:101-105` and `CMakeLists.txt:156-160` show that `WOLFMQTT_NONBLOCK` and `WOLFMQTT_MT` default to `no`.

## Implementation Behavior

In the default synchronous path, a QoS 1 `PUBLISH` is written and wolfMQTT waits for the corresponding `PUBACK`. If that wait times out, the call returns `MQTT_CODE_ERROR_TIMEOUT`; the connection remains marked as connected, and no global outbound in-flight Packet Identifier table retains the identifier until `PUBACK` is processed. A later call can pass the same `publish->packet_id`, and the encoder will write another packet using that identifier.

## Inconsistency Reason

The standard requires a QoS 1 `PUBLISH` Packet Identifier to remain unavailable until the client processes `PUBACK`. wolfMQTT's default path only checks that the identifier is nonzero, so a second new QoS 1 `PUBLISH` can be written with the same identifier while the first publication remains unacknowledged.

## Runtime Evidence

A focused client probe was compiled and run. It connected a client, sent one QoS 1 `PUBLISH`, withheld `PUBACK`, then sent a second different QoS 1 `PUBLISH` with the same Packet Identifier and recorded the emitted wire packets.

```text
build_exit_code=0
wire_connect_len=22
wire_publish[1]: len=18 qos=1 dup=0 packet_id=0x1234 topic=sensor/temp payload=A
wire_publish[2]: len=22 qos=1 dup=0 packet_id=0x1234 topic=sensor/humidity payload=B
rc_init=0 rc_net=0 rc_conn=0 rc1=-7 rc2=-7
MQTT_CODE_ERROR_TIMEOUT=-7 MQTT_CODE_ERROR_PACKET_ID=-5
connect_count=1 disconnect_count=0 read_count=4 write_count=3 publish_count=2
summary: ids=1234,1234 topics=sensor/temp,sensor/humidity payloads=A,B
flags_after=0x00000001 connected_after=1
RESULT=reproduced_reused_qos1_publish_packet_id_before_puback
run_exit_code=1
```

The two `PUBLISH` packets have different topics and payloads, and both have `dup=0`; the second packet is therefore a new application message, not a retransmission of the first packet. Both emitted QoS 1 `PUBLISH` packets use Packet Identifier `0x1234`, and no `PUBACK` was processed between them. The probe uses `run_exit_code=1` as the expected issue-found marker, not as a crash indicator.

## Impact

A broker can observe two different QoS 1 publications using the same Packet Identifier at the same time. The resulting `PUBACK` association becomes ambiguous, and the Packet Identifier lifecycle violates MQTT 3.1.1.

## Fix Direction

Track outbound in-flight Packet Identifiers in all client builds. For a new QoS > 0 `PUBLISH`, `SUBSCRIBE`, or `UNSUBSCRIBE`, reject an identifier that is still in use before the packet is encoded or written. Release it only after the corresponding `PUBACK`, `PUBCOMP`, `SUBACK`, or `UNSUBACK` has been processed, or after the connection/session state is intentionally cleared.
