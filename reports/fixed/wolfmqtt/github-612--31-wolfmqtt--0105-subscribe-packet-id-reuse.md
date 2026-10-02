# SUBSCRIBE can reuse an unacknowledged Packet Identifier

## Summary

In the default non-`WOLFMQTT_MULTITHREAD` blocking client path, a SUBSCRIBE that times out waiting for SUBACK leaves no outbound in-flight Packet Identifier state. A second new SUBSCRIBE can then be sent with the same Packet Identifier before any SUBACK is processed.

## Standard Requirement

Official standard: [MQTT Version 3.1.1](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html), Section 2.3.1 `Packet Identifier` and Sections 3.8/3.9 `SUBSCRIBE`/`SUBACK`.

```text
[MQTT-2.3.1-2] Each time a Client sends a new packet of one of these types it MUST assign it a currently unused Packet Identifier.
```

The same paragraph states that a Packet Identifier becomes available only after the client processes the corresponding acknowledgement. For SUBSCRIBE, that acknowledgement is SUBACK.

## Relevant Source Code

`implementions/wolfMQTT-master/src/mqtt_packet.c:2860-2863`

```c
/* [MQTT-2.3.1-1] SUBSCRIBE packets require a non-zero packet identifier */
if (subscribe->packet_id == 0) {
    return MQTT_TRACE_ERROR(MQTT_CODE_ERROR_PACKET_ID);
}
```

The encoder rejects only zero Packet Identifiers. It does not check whether the ID is already in use.

`implementions/wolfMQTT-master/src/mqtt_client.c:3336-3348`

```c
#ifdef WOLFMQTT_MULTITHREAD
rc = wm_SemLock(&client->lockClient);
if (rc == 0) {
    /* inform other threads of expected response */
    rc = MqttClient_RespList_Add(client, MQTT_PACKET_TYPE_SUBSCRIBE_ACK,
        subscribe->packet_id, &subscribe->pendResp, &subscribe->ack);
    wm_SemUnlock(&client->lockClient);
}
if (rc != 0) {
    MqttWriteStop(client, &subscribe->stat);
    return rc; /* Error locking client */
}
#endif
```

`implementions/wolfMQTT-master/src/mqtt_client.c:577-604`

```c
/* Verify newResp is not already in the list, and enforce MQTT Packet
 * Identifier in-use uniqueness ... */
if (packet_id != 0 && tmpResp->packet_id == packet_id) {
    return MQTT_TRACE_ERROR(MQTT_CODE_ERROR_PACKET_ID);
}
```

The collision guard exists only under `WOLFMQTT_MULTITHREAD`. A normal non-multithread build has no equivalent outbound in-flight ID table.

`implementions/wolfMQTT-master/src/mqtt_client.c:3376-3379`, `implementions/wolfMQTT-master/src/mqtt_client.c:3418-3419`

```c
rc = MqttClient_WaitType(client, &subscribe->ack,
    MQTT_PACKET_TYPE_SUBSCRIBE_ACK, subscribe->packet_id,
    client->cmd_timeout_ms, NULL);
```

```c
/* reset state */
subscribe->stat.write = MQTT_MSG_BEGIN;
```

After a terminal timeout (`MQTT_CODE_ERROR_TIMEOUT`), the subscribe object is reset, but the client has not recorded that the sent Packet Identifier is still awaiting SUBACK.

## Implementation Behavior

For a non-multithread MQTT 3.1.1 blocking client:

1. `MqttClient_Subscribe` encodes and writes a SUBSCRIBE using caller-supplied `subscribe->packet_id`.
2. If no SUBACK arrives, `MqttClient_WaitType` returns `MQTT_CODE_ERROR_TIMEOUT`.
3. The subscribe state is reset to `MQTT_MSG_BEGIN`.
4. A later new SUBSCRIBE with the same Packet Identifier is encoded and written again.

## Inconsistency Reason

The standard requires the Packet Identifier to remain unavailable until SUBACK is processed. wolfMQTT enforces this uniqueness only in the multithread pending-response list. In the default non-multithread path, timeout does not process SUBACK and does not keep the Packet Identifier reserved, so the library can send a second new SUBSCRIBE with a still-unacknowledged ID.

## Runtime Evidence

A focused client probe was compiled and run with `WOLFMQTT_MULTITHREAD`, `WOLFMQTT_NONBLOCK`, TLS, MQTT-SN, and MQTT v5 disabled to exercise the MQTT 3.1.1 blocking client path. It completed `MqttClient_NetConnect` and `MqttClient_Connect` with a successful CONNACK, then sent two new `SUBSCRIBE` packets with `packet_id=0x1234`. The mock network never returned `SUBACK`.

Observed output:

```text
net_connect_rc=0
wire_connect: len=22
mqtt_connect_rc=0 connack_return=0
wire_subscribe[1]: len=10 packet_id=0x1234
first_subscribe_rc=-7
wire_subscribe[2]: len=10 packet_id=0x1234
second_subscribe_rc=-7
summary: writes=3 reads=4 subscribes=2 ids=1234,1234
ISSUE_REPRODUCED: second new SUBSCRIBE reused packet_id 0x1234 before any SUBACK was processed.
probe_exit_code=2
```

`MQTT_CODE_ERROR_TIMEOUT` is `-7` in `implementions/wolfMQTT-master/wolfmqtt/mqtt_types.h:214`. The two `wire_subscribe` lines prove that the duplicate Packet Identifier was actually sent before any SUBACK was processed.

## Impact

A broker can receive two different SUBSCRIBE requests with the same Packet Identifier while the first one is still unacknowledged. This violates the Packet Identifier lifetime rule and can confuse SUBACK correlation or subscription state updates.

## Fix Direction

Track outbound in-flight Packet Identifiers in all client builds, not only under `WOLFMQTT_MULTITHREAD`. Reserve the ID before sending a new QoS>0 PUBLISH, SUBSCRIBE, or UNSUBSCRIBE; release it only after the matching acknowledgement is processed or after the connection/session is torn down.
