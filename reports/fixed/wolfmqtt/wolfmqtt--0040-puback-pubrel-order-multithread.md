# PUBACK/PUBREL order can be corrupted under WOLFMQTT_MULTITHREAD

## Summary

MQTT 3.1.1 requires a client to send QoS responses in the order that the corresponding messages are received. For QoS 1 inbound `PUBLISH`, the client must send `PUBACK` in receive order. The analogous QoS 2 response path must not let a later response overwrite an earlier unsent response. In wolfMQTT's `WOLFMQTT_MULTITHREAD` path, the pending response is stored in shared `client->packetAck`; the receive lock is then released before the response is encoded and written. Another waiter can process the next packet in that window and overwrite the shared response, so the first Packet Identifier may be skipped or acknowledged out of order. The ordinary single-threaded path remains sequential; the issue is scoped to this multithreaded scheduling window.

## Standard Requirement

Official standard: [MQTT Version 3.1.1](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html)

MQTT 3.1.1 Section 4.6, Message ordering:

```text
A Client MUST follow these rules when implementing the protocol flows defined elsewhere in this chapter:
It MUST send PUBACK packets in the order in which the corresponding PUBLISH packets were received (QoS 1 messages) [MQTT-4.6.0-2]
```

Therefore, if a client receives `PUBLISH(id=1)` and then `PUBLISH(id=2)`, it must send `PUBACK(id=1)` before `PUBACK(id=2)`. The same implementation principle applies to QoS 2 response state: a later pending response must not overwrite an earlier one before the earlier response is sent.

## Relevant Source Code

Shared ACK state and multithread locks are declared in `implementions/wolfMQTT-master/wolfmqtt/mqtt_client.h:255-298`:

```c
MqttPkRead   packet;    /* publish packet state - protected by read lock */
MqttPublishResp packetAck; /* publish ACK - protected by write lock */
MqttSk       read;      /* read socket state - protected by read lock */
MqttSk       write;     /* write socket state - protected by write lock */

#ifdef WOLFMQTT_MULTITHREAD
    wm_Sem lockSend;
    wm_Sem lockRecv;
    wm_Sem lockClient;
#endif
```

Receiving a `PUBLISH` prepares the response in `implementions/wolfMQTT-master/src/mqtt_client.c:1173-1216`:

```c
rc = MqttClient_Publish_ReadPayload(client, publish, timeout_ms);

resp->packet_type = (packet_qos == MQTT_QOS_1) ?
    MQTT_PACKET_TYPE_PUBLISH_ACK :
    MQTT_PACKET_TYPE_PUBLISH_REC;
resp->packet_id = packet_id;
```

The response is copied into shared `client->packetAck`, and then the read lock is released, in `implementions/wolfMQTT-master/src/mqtt_client.c:1895-1910`:

```c
/* setup ACK in shared context */
XMEMCPY(&client->packetAck, &resp, sizeof(MqttPublishResp));

mms_stat->read = MQTT_MSG_ACK;
mms_stat->ack = MQTT_MSG_WAIT;

/* done reading */
MqttReadStop(client, mms_stat);
```

`MqttReadStop` releases `lockRecv` under `WOLFMQTT_MULTITHREAD` in `implementions/wolfMQTT-master/src/mqtt_client.c:434-438`:

```c
stat->isReadActive = 0;
#ifdef WOLFMQTT_MULTITHREAD
    wm_SemUnlock(&client->lockRecv);
#endif
```

The write lock and ACK encoding happen after the receive lock has already been released, and encoding still reads the shared `packetAck`, in `implementions/wolfMQTT-master/src/mqtt_client.c:1951-1991`:

```c
if ((rc = MqttWriteStart(client, mms_stat)) != 0) {
    break;
}

rc = MqttEncode_PublishResp(client->tx_buf, client->tx_buf_len,
    client->packetAck.packet_type, &client->packetAck);

rc = MqttPacket_Write(client, client->tx_buf, xfer);
```

This permits the following interleaving: thread A receives `PUBLISH(id=1)`, writes `packetAck`, and releases `lockRecv`; thread B receives `PUBLISH(id=2)` and overwrites `packetAck`; thread A then encodes its ACK and reads `id=2`.

## Runtime Evidence

A focused multithread build probe was compiled and run. First, it exercised a sequential control case with two inbound QoS 1 PUBLISH packets. Then it forced the scheduling window by reentering message processing after `packetAck` was set for the first packet but before that ACK was encoded and written.

```text
build_exit_code=0
sequential rc_init=0 rc_net=0 rc1=0 rc2=0 write_count=2 read_count=4 msg_cb_count=2 ok=1
sequential write[0]=40 02 00 01 packet_id=1
sequential write[1]=40 02 00 02 packet_id=2
reordered rc_init=0 rc_net=0 rc1=0 reenter_rc=0 write_count=2 read_count=4 msg_cb_count=2 issue=1
reordered write[0]=40 02 00 02 packet_id=2
reordered write[1]=40 02 00 02 packet_id=2
MQTT_CODE_SUCCESS=0 MQTT_CODE_ERROR_SYSTEM=-14
summary sequential_ok=1 reordered_issue=1
RESULT=reproduced_puback_order_can_be_violated
run_exit_code=1
```

`40 02 00 01` is `PUBACK(id=1)`, and `40 02 00 02` is `PUBACK(id=2)`. The sequential control emits `1, 2`, as required. In the reordered window, the emitted ACKs become `2, 2`, which shows that the first ACK was overwritten before it was sent. The probe uses `run_exit_code=1` as the expected issue-found marker, not as a crash indicator.

## Impact

A broker may receive the later `PUBACK` or `PUBREL` first, or may never receive the response for the earlier Packet Identifier. That can leave QoS 1 or QoS 2 in-flight state pending, trigger unnecessary retransmission, or delay message processing.

## Fix Direction

Do not keep an unsent QoS response in a shared field that a later receive operation can overwrite. The response can be bound to the current wait/stat object, placed in a FIFO response queue that is drained under the send lock, or otherwise protected so a later read cannot replace it before the earlier response is written.
