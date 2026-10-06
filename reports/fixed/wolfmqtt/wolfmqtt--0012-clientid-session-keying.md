# ClientId change can reuse client-side QoS2 session state

## Summary

wolfMQTT keeps client-side inbound QoS2 session state in `MqttClient.recv_qos2_pending`, but does not bind that state to the ClientId used to create it. If an application reuses the same `MqttClient` object with a different ClientId and receives `Session Present=1`, stale QoS2 packet IDs can suppress delivery of a new message for the new ClientId.

## Standard Requirement

- MQTT 3.1.1: <https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html>
- Sections: `3.1.3.1 Client Identifier`, `3.1.2.4 Clean Session`, `4.1 Storing state`, `4.4 Message delivery retry`

Key excerpt:

```text
The ClientId MUST be used by Clients and by Servers to identify state
```

The standard defines client-held session state as uncompleted QoS1/QoS2 outbound messages and uncompleted QoS2 inbound messages. With `CleanSession=0`, stored session state is tied to the ClientId; `Session Present` tells the client whether the server found stored state for the supplied ClientId.

## Relevant Source Code

`wolfmqtt/mqtt_packet.h:470-472` carries the ClientId only in the CONNECT request object:

```c
word16      keep_alive_sec;
byte        clean_session;
const char *client_id;
```

`wolfmqtt/mqtt_client.h:405-420` documents `MqttClient_Connect` as using the supplied `MqttConnect` parameters; it does not state that changing ClientId requires reinitializing `MqttClient`.

`wolfmqtt/mqtt_client.h:255-261` and `wolfmqtt/mqtt_client.h:331-336` keep runtime packet/QoS state on `MqttClient`, but there is no stored ClientId associated with that state:

```c
MqttPkRead   packet;
MqttPublishResp packetAck;
MqttObject   msg;
word16 recv_qos2_pending[MQTT_MAX_RECV_QOS2];
```

`src/mqtt_client.c:2627-2639` clears inbound QoS2 state only when `Session Present` is zero:

```c
if (rc == MQTT_CODE_SUCCESS &&
        !(mc_connect->ack.flags & MQTT_CONNECT_ACK_FLAG_SESSION_PRESENT)) {
    XMEMSET(client->recv_qos2_pending, 0,
        sizeof(client->recv_qos2_pending));
}
```

`src/mqtt_client.c:2671-2690` uses that table to suppress duplicate QoS2 PUBLISH delivery:

```c
int suppress_cb = (publish->qos == MQTT_QOS_2 &&
    MqttClient_RecvQos2_Contains(client, publish->packet_id));

if (client->msg_cb
    && !suppress_cb
) {
```

Broker-side session state is keyed by ClientId, so this report is scoped to the client path. For example, `src/mqtt_broker.c:6167-6195` stores `bc->client_id`, and `src/mqtt_broker.c:6717-6781` restores or removes session state using that value.

## Implementation Behavior

On CONNECT success, wolfMQTT trusts `Session Present=1` as proof that the current client-side QoS2 table still belongs to the same MQTT Session. It never compares the new `mc_connect->client_id` with the ClientId that produced the existing `recv_qos2_pending` entries. Therefore a reused `MqttClient` can carry QoS2 state from ClientId A into ClientId B.

## Inconsistency Reason

The standard requires session state held by the Client to be identified by ClientId. wolfMQTT has client-held QoS2 session state, but identifies it only by `MqttClient` instance and Packet Identifier. A new ClientId with `Session Present=1` can inherit stale state from a different ClientId, so the client can treat a new QoS2 PUBLISH as a duplicate for the wrong session.

## Runtime Evidence

A focused client-side QoS 2 session-state probe was compiled and run. It exercised two controls and one cross-ClientId case: normal delivery without stale state, stale-state clearing when `Session Present=0`, and reuse of one `MqttClient` object across ClientId A and ClientId B while the server reports `Session Present=1`.

Build and run both completed successfully:

```text
build_exit_code=0
run_exit_code=0
```

Key output:

```text
control_no_stale result=delivered
control_fresh_session result=delivered
issue_cross_clientid connect_clientid=B rc=0 connack_flags=0x01 pending_qos2_id=9
issue_cross_clientid publish_after_connect rc=0 msg_cb_calls=0 pubrec_written=1 last_ack_type=5 last_ack_id=9
issue_cross_clientid result=cross_clientid_state_reused
```

Controls show that normal delivery works without stale state and that `Session Present=0` clears stale state. The issue case preloads a QoS2 pending ID from ClientId A, connects as ClientId B with `CleanSession=0` and `Session Present=1`, then receives a QoS2 PUBLISH with the same Packet Identifier. wolfMQTT sends PUBREC but does not call the message callback.

## Impact

A client application that reuses one `MqttClient` object across different ClientIds can silently drop a valid QoS2 message for the later ClientId when Packet Identifier values collide with stale inbound QoS2 state.

## Fix Direction

Store the active ClientId or a session key in `MqttClient` for client-held session state. On successful CONNECT, preserve `recv_qos2_pending` only when `Session Present=1`, `CleanSession=0`, and the ClientId matches the state owner; otherwise clear the table and other client-held session state.
