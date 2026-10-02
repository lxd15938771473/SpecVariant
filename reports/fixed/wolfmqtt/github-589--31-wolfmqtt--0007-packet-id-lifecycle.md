# Nonblocking client reuses in-flight Packet Identifier

## Summary

The confirmed scope is the client build with `WOLFMQTT_NONBLOCK` enabled and `WOLFMQTT_MULTITHREAD` disabled. In that configuration, wolfMQTT can send a new QoS1 `PUBLISH`, `SUBSCRIBE`, or `UNSUBSCRIBE` with the same Packet Identifier while a previous packet of the same kind is still waiting for `PUBACK`, `SUBACK`, or `UNSUBACK`.

The same reproducer does not trigger under `WOLFMQTT_NONBLOCK + WOLFMQTT_MULTITHREAD`: the second send returns `MQTT_CODE_ERROR_PACKET_ID` and no second frame is written. This report does not cover the broker/server path.

## Standard Requirement

Reference: [MQTT v3.1.1 specification, Sections 2.3.1, 4.3.2, and 4.3.3](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html).

```text
SUBSCRIBE, UNSUBSCRIBE, and PUBLISH (in cases where QoS > 0) Control Packets MUST contain a non-zero 16-bit Packet Identifier [MQTT-2.3.1-1]. Each time a Client sends a new packet of one of these types it MUST assign it a currently unused Packet Identifier [MQTT-2.3.1-2]. If a Client re-sends a particular Control Packet, then it MUST use the same Packet Identifier in subsequent re-sends of that packet. The Packet Identifier becomes available for reuse after the Client has processed the corresponding acknowledgement packet. In the case of a QoS 1 PUBLISH this is the corresponding PUBACK; in the case of QoS 2 it is PUBCOMP. For SUBSCRIBE or UNSUBSCRIBE it is the corresponding SUBACK or UNSUBACK [MQTT-2.3.1-3].
```

While an ACK is pending, a client may send other packets, but any new packet must use a different currently unused Packet Identifier. Reusing the same identifier is allowed only for retransmission of the same Control Packet.

## Relevant Source Code

`implementions/wolfMQTT-master/src/mqtt_client.c:560-604` compiles the Packet Identifier occupancy check only under `WOLFMQTT_MULTITHREAD`:

```c
#ifdef WOLFMQTT_MULTITHREAD
int MqttClient_RespList_Add(MqttClient *client,
    MqttPacketType packet_type, word16 packet_id, MqttPendResp *newResp,
    void *packet_obj)
{
    ...
    if (packet_id != 0 && tmpResp->packet_id == packet_id) {
        return MQTT_TRACE_ERROR(MQTT_CODE_ERROR_PACKET_ID);
    }
}
#endif
```

QoS `PUBLISH`, `SUBSCRIBE`, and `UNSUBSCRIBE` pending-response registration follows the same conditional pattern. In nonblocking mode, these APIs can return `MQTT_CODE_CONTINUE` while the ACK is still pending.

## Implementation Behavior

With `WOLFMQTT_NONBLOCK` but without `WOLFMQTT_MULTITHREAD`, `MqttClient_RespList_Add` and its duplicate Packet Identifier check are not compiled. The first send can return `MQTT_CODE_CONTINUE` before the ACK is processed. A caller can then pass a separate message object with the same Packet Identifier, and the code has no connection-level occupancy table to prevent a new Control Packet from being encoded and written.

With `WOLFMQTT_NONBLOCK + WOLFMQTT_MULTITHREAD`, the pending-response list retains the first packet's identifier and rejects the second send before it reaches the wire.

## Inconsistency Reason

MQTT 3.1.1 requires every new QoS>0 `PUBLISH`, `SUBSCRIBE`, and `UNSUBSCRIBE` to use a currently unused Packet Identifier until the corresponding ACK is processed. The nonblocking single-threaded client does not maintain this occupancy state, so it can send new in-flight Control Packets using an identifier that is still awaiting acknowledgment.

## Runtime Evidence

A focused reproducer was compiled in two configurations.

For `WOLFMQTT_NONBLOCK` without `WOLFMQTT_MULTITHREAD`, the second send returned `-101` and wrote a second new frame with the same Packet Identifier:

```text
WOLFMQTT_NONBLOCK=yes WOLFMQTT_MULTITHREAD=no rc1=-101 rc2=-101 sent_after_first=14 sent_total=28 write_calls=2
publish frame1 type=3 qos=1 packet_id=7 len=14
publish frame2 type=3 qos=1 packet_id=7 len=14
subscribe rc1=-101 rc2=-101 sent_after_first=14 sent_total=28 write_calls=2
subscribe frame1 type=8 qos=1 packet_id=9 len=14
subscribe frame2 type=8 qos=1 packet_id=9 len=14
unsubscribe rc1=-101 rc2=-101 sent_after_first=13 sent_total=26 write_calls=2
unsubscribe frame1 type=10 qos=1 packet_id=11 len=13
unsubscribe frame2 type=10 qos=1 packet_id=11 len=13
```

For `WOLFMQTT_NONBLOCK + WOLFMQTT_MULTITHREAD`, the second send returned `-5` and no second frame was written:

```text
WOLFMQTT_NONBLOCK=yes WOLFMQTT_MULTITHREAD=yes rc1=-101 rc2=-5 sent_after_first=14 sent_total=14 write_calls=1
publish frame1 type=3 qos=1 packet_id=7 len=14
subscribe rc1=-101 rc2=-5 sent_after_first=14 sent_total=14 write_calls=1
subscribe frame1 type=8 qos=1 packet_id=9 len=14
unsubscribe rc1=-101 rc2=-5 sent_after_first=13 sent_total=13 write_calls=1
unsubscribe frame1 type=10 qos=1 packet_id=11 len=13
```

Observed result: the single-threaded nonblocking build writes duplicate in-flight identifiers, while the multithreaded build's pending-response list prevents it.

## Impact

The nonblocking single-threaded client can place multiple new in-flight Control Packets with the same Packet Identifier on one connection. This violates MQTT 3.1.1 and can confuse ACK association for publish, subscribe, and unsubscribe flows.

## Fix Direction

Maintain connection-level Packet Identifier occupancy even when `WOLFMQTT_MULTITHREAD` is disabled. Reserve the identifier before sending a new QoS>0 `PUBLISH`, `SUBSCRIBE`, or `UNSUBSCRIBE`; reject conflicts with `MQTT_CODE_ERROR_PACKET_ID`; release the identifier only after the corresponding ACK has been processed.
