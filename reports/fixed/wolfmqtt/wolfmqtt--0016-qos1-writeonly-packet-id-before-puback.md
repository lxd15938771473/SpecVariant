# QoS1 WriteOnly PUBLISH releases Packet Identifier before PUBACK

## Summary

- Result: this path returns success, removes pending state, and allows immediate reuse of the same Packet Identifier before the corresponding `PUBACK` arrives.
- Control: ordinary `MqttClient_Publish` waits for the matching `PUBACK` and did not reproduce the issue.

## Standard Requirement

Reference: [MQTT v3.1.1 specification, Sections 2.3.1 and 4.3.2](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html).

```text
MUST treat the PUBLISH Packet as "unacknowledged" until it has received the corresponding PUBACK packet from the receiver.
```

Meaning: a QoS1 sender must keep the `PUBLISH` unacknowledged until the corresponding `PUBACK` is received. New QoS-related Control Packets must use currently unused Packet Identifiers, and the QoS1 identifier cannot be reused until the corresponding `PUBACK` has been processed.

## Relevant Source Code

`MqttClient_Publish` first registers the expected `PUBACK`:

```c
rc = MqttClient_RespList_Add(client, resp_type,
    publish->packet_id, &publish->pendResp, &publish->resp);
```

In the write-only path, when `WOLFMQTT_NONBLOCK` is not enabled, an unfinished ACK wait is converted into success:

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

The function then removes the pending item:

```c
MqttClient_RespList_Remove(client, &publish->pendResp);
```

The ordinary path differs because it waits for the matching ACK:

```c
rc = MqttClient_WaitType(client, &publish->resp, resp_type,
    publish->packet_id, client->cmd_timeout_ms, NULL);
```

Packet Identifier occupancy checks depend on the pending-response list.

## Implementation Behavior

`MqttClient_Publish` behaves correctly: without `PUBACK`, it returns an error; with the matching `PUBACK`, it succeeds.

The affected `MqttClient_Publish_WriteOnly` path returns `MQTT_CODE_SUCCESS` before `PUBACK` arrives and removes the pending entry. Because duplicate Packet Identifier checks consult the pending list, the same Packet Identifier is then treated as available.

## Inconsistency Reason

The standard requires a QoS1 `PUBLISH` to remain unacknowledged until `PUBACK` is received, and its Packet Identifier must not be reused before that. This implementation path deletes the only in-flight record before `PUBACK`, allowing a new QoS1 `PUBLISH` to reuse the identifier.

## Runtime Evidence

Two focused probes were compiled and run.

Ordinary publish control:

```text
without_puback rc=-7 writes=1 consumed=0/0 publish=32 07 00 01 61 12 34 68 69 stat=0 ack_id=0x0000
with_puback rc=0 writes=1 consumed=4/4 publish=32 07 00 01 61 12 34 68 69 stat=0 ack_id=0x1234
RESULT: no issue reproduced
```

Write-only reproducer:

```text
first rc=0 pending_after_first=0 stat=0 ack_id=0x0000
second_same_pid rc=0 pending_after_second=0 stat=0 ack_id=0x0000 writes=2
first_publish 32 06 00 01 61 12 34 78
second_publish 32 06 00 01 61 12 34 78
RESULT: issue reproduced
```

Observed result: the write-only path sent two QoS1 PUBLISH packets using the same Packet Identifier `0x1234` before any `PUBACK` was processed.

## Impact

The client can send a second new QoS1 `PUBLISH` with the same Packet Identifier while the first is still unacknowledged, making receiver state and later `PUBACK` ownership ambiguous.

## Fix Direction

Keep the pending entry in the write-only QoS1 path until the reader thread processes the corresponding `PUBACK`, or return a non-final result requiring the caller to keep the `MqttPublish` object until ACK completion. Add a regression test that reuse before `PUBACK` must fail.
