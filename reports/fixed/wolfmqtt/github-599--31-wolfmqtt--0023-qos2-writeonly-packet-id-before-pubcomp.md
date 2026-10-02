# QoS2 WriteOnly PUBLISH reuses Packet Identifier before PUBCOMP

## Summary

- Result: before QoS2 `PUBREC`/`PUBCOMP` arrives, this path returns success, clears pending state, and allows a new `PUBLISH` to reuse the same Packet Identifier.
- Control: ordinary `MqttClient_Publish` returns an error when no ACK arrives and does not report the publish as successful.

## Standard Requirement

Reference: [MQTT v3.1.1 specification, Sections 2.3.1 and 4.3.3](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html).

```text
MUST assign an unused Packet Identifier when it has a new Application Message to publish.
```

Meaning: a QoS2 sender must use an unused Packet Identifier for a new Application Message. That identifier becomes reusable only after the corresponding `PUBCOMP` has been received and processed.

## Relevant Source Code

QoS>0 publish registers a pending response; for QoS2 the expected terminal response is `MQTT_PACKET_TYPE_PUBLISH_COMP`:

```c
resp_type = (publish->qos == MQTT_QOS_1) ?
        MQTT_PACKET_TYPE_PUBLISH_ACK :
        MQTT_PACKET_TYPE_PUBLISH_COMP;

rc = MqttClient_RespList_Add(client, resp_type,
    publish->packet_id, &publish->pendResp, &publish->resp);
```

In write-only mode without `WOLFMQTT_NONBLOCK`, the unfinished wait is converted into success:

```c
if (writeOnly) {
    rc = MqttClient_CheckPendResp(client, resp_type, publish->packet_id);
#ifndef WOLFMQTT_NONBLOCK
    if (rc == MQTT_CODE_CONTINUE) {
        rc = MQTT_CODE_SUCCESS;
    }
#endif
}
```

The function then removes the pending entry:

```c
MqttClient_RespList_Remove(client, &publish->pendResp);
```

The ordinary path waits for the matching response. Packet Identifier duplicate checks depend on the pending-response list.

## Implementation Behavior

For ordinary `MqttClient_Publish`, QoS2 PUBLISH returns `MQTT_CODE_ERROR_TIMEOUT` when no ACK arrives. It does not claim success.

For `MqttClient_Publish_WriteOnly`, the same lack of `PUBREC`/`PUBCOMP` still returns `MQTT_CODE_SUCCESS` and deletes the pending entry. The same Packet Identifier is no longer considered occupied, so a second QoS2 `PUBLISH` can be sent with that identifier.

## Inconsistency Reason

The standard requires a new QoS2 `PUBLISH` to use an unused Packet Identifier, and the identifier must not be reused until `PUBCOMP`. The implementation removes in-flight state before `PUBCOMP`, allowing reuse while the QoS2 exchange is still incomplete.

## Runtime Evidence

A focused QoS2 Packet Identifier reuse probe was compiled and run.

Observed output:

```text
normal_no_ack rc=-7 pending=0 stat=0 ack_id=0x0000 writes=1
normal_publish 34 06 00 01 61 22 33 78
writeonly_first rc=0 pending_after_first=0 stat=0 ack_id=0x0000
writeonly_second_same_pid rc=0 pending_after_second=0 stat=0 ack_id=0x0000 writes=2
writeonly_first_publish 34 06 00 01 61 22 33 78
writeonly_second_publish 34 06 00 01 61 22 33 78
RESULT: issue reproduced
```

Observed result: `0x34` is QoS2 `PUBLISH`. Both write-only frames use Packet Identifier `0x2233`, and the second frame is sent before the first exchange reaches `PUBCOMP`.

## Impact

The client can have two different QoS2 PUBLISH flows using the same Packet Identifier at the same time, making later `PUBREC`/`PUBCOMP` ownership ambiguous and breaking MQTT 3.1.1 QoS2 in-flight rules.

## Fix Direction

Keep the pending entry in the write-only QoS2 path until the reader thread processes the corresponding `PUBCOMP`, or return a non-final result requiring the caller to keep the `MqttPublish` object until the QoS2 exchange completes. Add a regression test that Packet Identifier reuse before `PUBCOMP` fails.
