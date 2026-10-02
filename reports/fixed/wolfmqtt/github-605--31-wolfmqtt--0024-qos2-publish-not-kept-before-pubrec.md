# QoS2 PUBLISH is not kept unacknowledged before PUBREC

## Standard Requirement

Reference: [MQTT v3.1.1 specification, Sections 2.3.1 and 4.3.3](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html).

For a QoS2 Sender, the standard requires:

```text
MUST assign an unused Packet Identifier when it has a new Application Message to publish.
MUST send a PUBLISH packet containing this Packet Identifier with QoS=2, DUP=0.
MUST treat the PUBLISH packet as "unacknowledged" until it has received the corresponding PUBREC packet from the receiver.
The Packet Identifier becomes available for reuse once the Sender has received the PUBCOMP Packet.
```

Meaning: after QoS2 PUBLISH is sent and before the corresponding PUBREC arrives, that Packet Identifier is still in use. The same connection must not send a new `DUP=0` PUBLISH with that identifier.

## Code Check

`MqttClient_Publish` documents that QoS2 waits for `PUBLISH_REC`, sends `PUBLISH_REL`, and then waits for `PUBLISH_COMP`.

Pending response registration is compiled only under `WOLFMQTT_MULTITHREAD`:

```c
#ifdef WOLFMQTT_MULTITHREAD
rc = MqttClient_RespList_Add(client, resp_type,
    publish->packet_id, &publish->pendResp, &publish->resp);
#endif
```

The QoS2 path waits for the terminal `MQTT_PACKET_TYPE_PUBLISH_COMP`; intermediate PUBREC is handled inside the wait flow:

```c
resp_type = (publish->qos == MQTT_QOS_1) ?
    MQTT_PACKET_TYPE_PUBLISH_ACK :
    MQTT_PACKET_TYPE_PUBLISH_COMP;
rc = MqttClient_WaitType(client, &publish->resp, resp_type,
    publish->packet_id, client->cmd_timeout_ms, NULL);
```

On non-continue results, waiting state is cleaned up; with `WOLFMQTT_MULTITHREAD`, the pending response is also removed. Without an active pending-response list, the core API does not prevent another `MqttPublish` object from reusing the same packet id.

## Runtime Evidence

A focused probe used a fake network where writes succeed but reads intentionally do not return PUBREC. It then sent another QoS2 PUBLISH with `packet_id=7` on the same still-connected client.

The same probe was compiled and run under four configurations: default, `WOLFMQTT_NONBLOCK`, `WOLFMQTT_MULTITHREAD`, and `WOLFMQTT_NONBLOCK + WOLFMQTT_MULTITHREAD`.

Observed output:

```text
default:
first_rc=-7 second_same_id_rc=-7 connected_after_first=1 pending_after_first=0
first_publish  len=10 type=3 dup=0 qos=2 packet_id=7
second_publish len=10 type=3 dup=0 qos=2 packet_id=7

WOLFMQTT_NONBLOCK:
first_rc=-101 second_same_id_rc=-101 connected_after_first=1 pending_after_first=0
first_publish  len=10 type=3 dup=0 qos=2 packet_id=7
second_publish len=10 type=3 dup=0 qos=2 packet_id=7

WOLFMQTT_MULTITHREAD:
first_rc=-7 second_same_id_rc=-7 connected_after_first=1 pending_after_first=0
first_publish  len=10 type=3 dup=0 qos=2 packet_id=7
second_publish len=10 type=3 dup=0 qos=2 packet_id=7

WOLFMQTT_NONBLOCK + WOLFMQTT_MULTITHREAD:
first_rc=-101 second_same_id_rc=-5 connected_after_first=1 pending_after_first=1
first_publish  len=10 type=3 dup=0 qos=2 packet_id=7
second_publish len=0 type=0 dup=0 qos=0 packet_id=0
```

Observed result: `-7` is timeout, `-101` is continue, and `-5` is packet-id error. The first three configurations wrote a second `DUP=0` QoS2 PUBLISH using the same `packet_id=7` before PUBREC arrived while the connection was still active. The fourth configuration is the positive control: the pending-response list rejected the second send.

## Decision

In default, `WOLFMQTT_NONBLOCK`, and blocking `WOLFMQTT_MULTITHREAD` paths, wolfMQTT does not keep the sent QoS2 PUBLISH Packet Identifier in client-level unacknowledged state before PUBREC. The same connection can therefore reuse that identifier for a new `DUP=0` QoS2 PUBLISH.

## Fix Direction

Keep QoS2 Packet Identifiers reserved from the moment PUBLISH is written until the QoS2 exchange completes, at least through PUBREC and until PUBCOMP releases the identifier.
