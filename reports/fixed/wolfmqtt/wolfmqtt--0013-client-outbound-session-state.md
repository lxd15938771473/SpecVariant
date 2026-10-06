# Client outbound Session state is not preserved

## Summary

MQTT 3.1.1 requires the Client to keep QoS 1/2 messages already sent to the Server but not fully acknowledged as Session state. On a CleanSession=0 reconnect, those unacknowledged PUBLISH packets must be resent with the original Packet Identifier. wolfMQTT keeps inbound QoS 2 dedup state, but it has no client-side store for outbound unacknowledged PUBLISH/PUBREL across disconnect/reconnect. A runtime probe confirms that a QoS 1 PUBLISH written with packet id 77 is not replayed after a resumed CleanSession=0 session.

## Standard Requirement

Official standard: [MQTT Version 3.1.1](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html)

Section 3.1.2.4, CleanSession:

```text
If CleanSession is set to 0, the Server MUST resume communications with the Client based on state from the current Session (as identified by the Client identifier). If there is no Session associated with the Client identifier the Server MUST create a new Session. The Client and Server MUST store the Session after the Client and Server are disconnected [MQTT-3.1.2-4].

The Session state in the Client consists of:
QoS 1 and QoS 2 messages which have been sent to the Server, but have not been completely acknowledged.
QoS 2 messages which have been received from the Server, but have not been completely acknowledged.
```

Section 4.1, Storing state:

```text
The Client and Server MUST store Session state for the entire duration of the Session [MQTT-4.1.0-1].
```

Section 4.4, Message delivery retry:

```text
When a Client reconnects with CleanSession set to 0, both the Client and Server MUST re-send any unacknowledged PUBLISH Packets (where QoS > 0) and PUBREL Packets using their original Packet Identifiers [MQTT-4.4.0-1].
```

## Relevant Source Code

`implementions/wolfMQTT-master/wolfmqtt/mqtt_client.h:255`

```c
MqttPkRead   packet; /* publish packet state - protected by read lock */
MqttPublishResp packetAck; /* publish ACK - protected by write lock */
MqttSk       read;   /* read socket state - protected by read lock */
MqttSk       write;  /* write socket state - protected by write lock */
```

`implementions/wolfMQTT-master/wolfmqtt/mqtt_client.h:331`

```c
#if WOLFMQTT_MAX_QOS >= 2
    /* Inbound QoS 2 packet ids delivered to the application and awaiting their
     * PUBREL. A retransmitted PUBLISH with an id still listed here is answered
     * with PUBREC but not delivered a second time [MQTT-4.3.3-10]. Slot value 0
     * is empty; a QoS 2 packet id is never 0. */
    word16 recv_qos2_pending[MQTT_MAX_RECV_QOS2];
#endif
```

The client struct contains inbound QoS 2 pending state, but no corresponding outbound session queue for QoS 1/2 messages already sent to the Server and not acknowledged.

`implementions/wolfMQTT-master/src/mqtt_client.c:3023`

```c
/* Encode the publish packet */
rc = MqttEncode_Publish(client->tx_buf, client->tx_buf_len,
        publish, pubCb ? 1 : 0);
```

`implementions/wolfMQTT-master/src/mqtt_client.c:3154`

```c
/* if not expecting a reply then we are done */
if (publish->qos == MQTT_QOS_0) {
    break;
}
publish->stat.write = MQTT_MSG_WAIT;
```

QoS 1/2 PUBLISH waits for the acknowledgement in the caller's `MqttPublish` object; it is not copied into a durable client Session-state store.

`implementions/wolfMQTT-master/src/mqtt_client.c:4141`

```c
/* reset states */
mms_stat->write = MQTT_MSG_BEGIN;
mms_stat->read = MQTT_MSG_BEGIN;
```

`MqttClient_CancelMessage` resets message state, so an abandoned unacknowledged publish is not retained as resumable Session state.

`implementions/wolfMQTT-master/src/mqtt_client.c:4290`

```c
for (tmpResp = client->firstPendResp;
     tmpResp != NULL;
     tmpResp = nextResp) {
    nextResp = tmpResp->next;
    ...
    MqttClient_RespList_Remove(client, tmpResp);
}
```

In multithread builds, `MqttClient_NetDisconnect` removes pending responses on disconnect. This clears current-connection wait state rather than preserving outbound unacknowledged PUBLISH/PUBREL for a CleanSession=0 resume.

## Implementation Behavior

For inbound QoS 2, wolfMQTT preserves `recv_qos2_pending` when the server resumes a session and clears it only for fresh sessions (`src/mqtt_client.c:2635`). That satisfies the client-side received-QoS2 part.

For outbound QoS 1/2, the client publishes from the caller-provided `MqttPublish` object, waits for ACK, and on failure/cancel resets local message state. There is no client-owned session queue that survives disconnect and automatically resends unacknowledged PUBLISH/PUBREL with the original Packet Identifier after a CleanSession=0 reconnect.

## Inconsistency Reason

The standard defines client Session state to include QoS 1/2 messages sent to the Server but not completely acknowledged. It also requires those packets to be resent on CleanSession=0 reconnect. wolfMQTT implements only transient publish state and inbound QoS 2 dedup storage on the client side. Once the connection is lost before ACK completion, outbound unacknowledged PUBLISH state is not retained for automatic replay, so the client cannot meet `MQTT-4.1.0-1` together with `MQTT-4.4.0-1`.

## Runtime Evidence

A focused client probe was compiled and run. It sent a QoS 1 `PUBLISH` with Packet Identifier `77`, forced the ACK wait to fail, then reconnected with `clean_session=0` and a `CONNACK` whose Session Present flag was set. The probe inspected the next client write after reconnect to determine whether wolfMQTT replayed the unacknowledged publish.

Observed result:

```text
compile_exit_code=0
client_probe_exit_code=0
=== Test Suite: reqaf54_session_state ===
  [PASS] qos1_sent_unacked_publish_not_replayed_on_clean0_resume
Total Passed: 1
Total Failed: 0
ALL TESTS PASSED
```

The probe sends QoS 1 PUBLISH packet id 77, forces the ACK wait to fail, then reconnects with `clean_session=0` and a CONNACK with Session Present set. The next client write is CONNECT (`0x10`), not a replayed PUBLISH with DUP/QoS1 (`0x3A`) and packet id 77.

## Impact

A client using persistent sessions can lose its own outbound QoS 1/2 delivery state across reconnect. Messages already sent but not acknowledged may not be retried, weakening MQTT 3.1.1 QoS guarantees.

## Fix Direction

Add client-side outbound Session-state storage for unacknowledged QoS 1/2 PUBLISH and QoS 2 PUBREL when CleanSession=0 is in effect. On a resumed session, replay those packets with the original Packet Identifier; for PUBLISH retransmission, set DUP=1. Clear each stored entry only when the matching acknowledgement completes the QoS flow or when the Session is deliberately discarded.
