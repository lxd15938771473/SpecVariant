# QoS 0 PUBLISH Replayed on Persistent Reconnect

## Summary

This is a real broker-side issue. Client-side QoS 0 publish returns after the packet write, but the dynamic broker outbound queue can keep a QoS 0 fan-out entry after a partial write failure, move it into a persistent orphan session, and send it again after the subscriber reconnects.

## Standard Requirement

- Official standard: [MQTT Version 3.1.1](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html)
- Section: `4.3.1 QoS 0: At most once delivery`
- Related section: `4.4 Message delivery retry`

```text
The message is delivered according to the capabilities of the underlying network. No response is sent by the receiver and no retry is performed by the sender. The message arrives at the receiver either once or not at all.

When a Client reconnects with CleanSession set to 0, both the Client and Server MUST re-send any unacknowledged PUBLISH Packets (where QoS > 0) and PUBREL Packets using their original Packet Identifiers [MQTT-4.4.0-1].
```

QoS 0 allows no receiver response and no sender retry. Reconnect redelivery is limited to QoS > 0 PUBLISH and PUBREL, so a QoS 0 PUBLISH must not be replayed through session recovery.

## Relevant Source Code

Direct client publish is not the faulty path: after writing payload, QoS 0 exits before ACK wait.

```c
/* implementions/wolfMQTT-master/src/mqtt_client.c:3133 */
rc = MqttClient_Publish_WritePayload(client, publish, pubCb);
MqttWriteStop(client, &publish->stat);
if (rc < 0) {
    MqttClient_CancelMessage(client, (MqttObject*)publish);
    break;
}
/* if not expecting a reply then we are done */
if (publish->qos == MQTT_QOS_0) {
    break;
}
publish->stat.write = MQTT_MSG_WAIT;
```

The broker live fan-out path queues every effective QoS level; only QoS 1/2 get packet IDs.

```c
/* implementions/wolfMQTT-master/src/mqtt_broker.c:7760 */
BrokerOutPub* e = BrokerOutPub_Alloc(topic, payload,
                      pub.total_len
#ifdef WOLFMQTT_V5
                      , pub.props
#endif
                      );
e->qos = eff_qos;
if (eff_qos >= MQTT_QOS_1) {
    e->packet_id = BrokerNextPacketIdForQueue(broker, wc->out_q_head);
}
e->state = BROKER_OUTQ_QUEUED;
BrokerClient_EnqueueOutPub(wc, e);
if (wc != bc) {
    BrokerClient_DrainOutQueue(wc);
}
```

The queue invariant says dynamic QoS 0 entries are deleted only after the PUBLISH is written.

```c
/* implementions/wolfMQTT-master/wolfmqtt/mqtt_broker.h:464 */
Dynamic-memory QoS 0 entries are deleted as soon as the PUBLISH is written;
they never leave the QUEUED state or increment the inflight counter.
```

On partial write continuation, QoS 0 remains queued. On later write error, the entry is still not deleted; deletion happens only after a complete write.

```c
/* implementions/wolfMQTT-master/src/mqtt_broker.c:2125 */
if (wr_rc == MQTT_CODE_CONTINUE) {
    bc->out_q_pending_len = enc_rc;
    if (cur->qos > MQTT_QOS_0 && bc->client.write.pos > 0) {
        cur->retransmit_dup = 1;
    }
    return wr_rc;
}
bc->out_q_pending_len = 0;
if (wr_rc != enc_rc) {
    return (wr_rc < 0) ? wr_rc : MQTT_CODE_ERROR_NETWORK;
}

/* implementions/wolfMQTT-master/src/mqtt_broker.c:2161 */
if (cur->qos == MQTT_QOS_0) {
    BrokerOutPub* free_me = cur;
    if (prev == NULL) {
        bc->out_q_head = cur->next;
    }
    else {
        prev->next = cur->next;
    }
    if (bc->out_q_tail == cur) {
        bc->out_q_tail = prev;
    }
    bc->out_q_count--;
    cur = cur->next;
    BrokerOutPub_Free(free_me);
}
```

Persistent reconnect moves the whole queue into an orphan, then back to the new client and drains it.

```c
/* implementions/wolfMQTT-master/src/mqtt_broker.c:7980 */
static void BrokerClient_AbnormalClose(MqttBroker* broker, BrokerClient* bc)
{
    BrokerClient_PublishWill(broker, bc);
    if (bc->session_expiry_sec == 0) {
        BrokerSubs_EndClientSession(broker, bc);
    }
    else {
        BrokerSubs_OrphanClient(broker, bc);
    }
    BrokerClient_Remove(broker, bc);
}

/* implementions/wolfMQTT-master/src/mqtt_broker.c:3555 */
o->out_q_head     = bc->out_q_head;
o->out_q_tail     = bc->out_q_tail;
o->out_q_count    = bc->out_q_count;

/* implementions/wolfMQTT-master/src/mqtt_broker.c:3625 */
new_bc->out_q_head     = o->out_q_head;
new_bc->out_q_tail     = o->out_q_tail;
new_bc->out_q_count    = o->out_q_count;

/* implementions/wolfMQTT-master/src/mqtt_broker.c:7016 */
if (bc->out_q_count > 0) {
    BrokerClient_DrainOutQueue(bc);
}
```

By contrast, newly published messages for already-offline dynamic sessions explicitly drop QoS 0.

```c
/* implementions/wolfMQTT-master/src/mqtt_broker.c:7682 */
if (eff_qos > MQTT_QOS_0) {
    BrokerOrphan_Enqueue(broker, o, topic, payload, pub.total_len, eff_qos, 0);
}
```

## Implementation Behavior

In the dynamic broker path, a connected subscriber's QoS 0 fan-out entry can survive a partial write and then a write failure while still in `BROKER_OUTQ_QUEUED`. Because persistent abnormal close preserves the whole `out_q`, that QoS 0 entry is reclaimed on reconnect and drained again.

## Inconsistency Reason

The standard prohibits QoS 0 retry and permits reconnect redelivery only for QoS > 0 PUBLISH/PUBREL. wolfMQTT can resend a QoS 0 PUBLISH after persistent-session recovery, so the behavior is inconsistent with MQTT 3.1.1 QoS 0 at-most-once delivery.

## Runtime Evidence

A focused broker probe was compiled and run. It connected a persistent-session subscriber, delivered a QoS 0 fan-out message, forced a partial write followed by a write error, closed the subscriber abnormally so the outbound queue became orphan-session state, and then reconnected the subscriber to observe whether the QoS 0 message was replayed.

Result:

```text
build exit code: 0
after_subscribe: closed=0 out_len=9 publishes=0 bytes=20 02 00 00 90 03 00 01 00
after_partial_qos0: closed=0 out_len=10 publishes=0 bytes=20 02 00 00 90 03 00 01 00 30
after_write_error: closed=1 out_len=17 publishes=1 bytes=20 02 00 00 90 03 00 01 00 30 06 00 01 78 41 42 43
after_reconnect: closed=0 out_len=12 publishes=1 bytes=20 02 01 00 30 06 00 01 78 41 42 43
probe result: issue reproduced; QoS0 PUBLISH was replayed on reconnect
```

The frame `30 06 00 01 78 41 42 43` is QoS 0 PUBLISH(topic `x`, payload `ABC`). It appears before the write failure and again after reconnect, proving replay.

## Impact

A persistent-session subscriber can receive duplicate QoS 0 application messages after broker-side partial write failure and reconnect, breaking at-most-once delivery.

## Fix Direction

Do not preserve QoS 0 queued entries across abnormal close/session orphaning. Drop them after a send attempt or filter them before moving `out_q` into an orphan; reconnect replay should keep only QoS > 0 PUBLISH/PUBREL state.
