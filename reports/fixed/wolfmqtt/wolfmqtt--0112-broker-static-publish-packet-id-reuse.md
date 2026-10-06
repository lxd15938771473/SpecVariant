# Broker static QoS1/QoS2 PUBLISH can reuse Packet Identifier before ACK completion

## Summary

In static-memory broker builds, live fan-out sends QoS1/QoS2 `PUBLISH` packets directly with `BrokerNextPacketId(broker)`. That generator only increments and wraps; it does not remember Packet Identifiers still waiting for subscriber `PUBACK` or `PUBCOMP`. After wraparound, the broker can reuse the same outgoing Packet Identifier for a new QoS>0 `PUBLISH` before the first delivery flow completes.

## Standard Requirement

Official standard: [MQTT Version 3.1.1](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html), Section 2.3.1 "Packet Identifier"

```text
Each time a Client sends a new packet of one of these types it MUST assign it a currently unused Packet Identifier [MQTT-2.3.1-2].
The Packet Identifier becomes available for reuse after the Client has processed the corresponding acknowledgement packet. In the case of QoS 2 it is PUBCOMP [MQTT-2.3.1-3].
The same conditions apply to a Server when it sends a PUBLISH with QoS > 0 [MQTT-2.3.1-4].
```

Meaning: for a Server-to-Client QoS2 `PUBLISH`, the Packet Identifier remains in use until the Server receives/processes the corresponding `PUBCOMP`.

## Relevant Source Code

`implementions/wolfMQTT-master/src/mqtt_broker.c:4343`

```c
#ifdef WOLFMQTT_STATIC_MEMORY
static word16 BrokerNextPacketId(MqttBroker* broker)
{
    word16 id = broker->next_packet_id;

    if (id == 0) {
        id = 1;
    }
    broker->next_packet_id = (word16)(id + 1);
    if (broker->next_packet_id == 0) {
        broker->next_packet_id = 1;
    }
    return id;
}
```

Static-memory `BrokerNextPacketId` returns the next numeric id and wraps to `1`; it does not check whether that id is still outstanding for the subscriber.

`implementions/wolfMQTT-master/src/mqtt_broker.c:7569`

```c
/* Static-memory mode keeps the legacy synchronous
 * fan-out: no per-subscriber queue, no inflight cap.
 * Sub-encoder failure is logged but not propagated. */
XMEMSET(&out_pub, 0, sizeof(out_pub));
out_pub.topic_name = topic;
out_pub.qos = eff_qos;
if (eff_qos >= MQTT_QOS_1) {
    out_pub.packet_id = BrokerNextPacketId(broker);
}
...
sub_rc = MqttEncode_Publish(sub->client->tx_buf,
    BROKER_CLIENT_TX_SZ(sub->client), &out_pub, 0);
```

This is the live connected-subscriber static fan-out path. QoS1/QoS2 outbound `PUBLISH` packets are sent directly, without a per-subscriber in-flight table.

`implementions/wolfMQTT-master/src/mqtt_broker.c:8328`

```c
case MQTT_PACKET_TYPE_PUBLISH_COMP:
{
    ...
    comp_rc = MqttDecode_PublishResp(bc->rx_buf, rc,
            MQTT_PACKET_TYPE_PUBLISH_COMP, &comp_resp);
    if (comp_rc >= 0) {
    #ifdef WOLFMQTT_STATIC_MEMORY
        BrokerStaticOrphan_OnPubComp(broker, bc,
            comp_resp.packet_id);
    #else
        BrokerClient_OnPubComp(bc, comp_resp.packet_id);
    #endif
    }
```

In static-memory builds, `PUBCOMP` completion updates only the static orphan-session queue. A live direct fan-out delivery was never inserted there, so there is no live in-use id to release.

`implementions/wolfMQTT-master/src/mqtt_broker.c:4369`

```c
static word16 BrokerNextPacketIdForQueue(MqttBroker* broker,
    const BrokerOutPub* out_q)
{
    ...
    do {
        if (!BrokerPacketIdInQueue(out_q, candidate)) {
            broker->next_packet_id = (word16)(candidate + 1);
            ...
            return candidate;
        }
        candidate++;
        ...
    } while (candidate != first);
```

Dynamic-memory mode has a queue-aware allocator and removes QoS2 entries from `out_q` only in `BrokerClient_OnPubComp`. The issue is therefore specific to the static-memory live direct-send path.

## Implementation Behavior

For a live static-memory subscriber:

1. Broker forwards each matching QoS2 message immediately.
2. It assigns the outbound Packet Identifier with the global wrapping counter.
3. It does not store that outbound id as in-use while waiting for subscriber `PUBREC` / `PUBCOMP`.
4. When the counter wraps, an earlier QoS2 id can be reused even though no `PUBCOMP` has arrived for the first use.

## Inconsistency Reason

MQTT-2.3.1-4 applies the client Packet Identifier lifetime rule to Server-sent QoS>0 `PUBLISH`. MQTT-2.3.1-3 keeps a QoS1 Packet Identifier unavailable until `PUBACK` and a QoS2 Packet Identifier unavailable until `PUBCOMP`. The static live fan-out path does not retain outbound QoS1/QoS2 Packet Identifiers across the handshake, so it cannot know whether a wrapped id is still unavailable. This allows a new Server-to-Client QoS>0 `PUBLISH` to reuse an id before the prior acknowledgement flow completes, which violates the requirement.

## Runtime Evidence

A focused static-memory broker probe was compiled and run. It drove 65,536 incoming QoS 2 publishes through live broker fan-out while the subscriber did not send `PUBCOMP` for the broker's outgoing QoS 2 deliveries. The probe recorded the Packet Identifier assigned to each outgoing subscriber `PUBLISH` and checked whether the first identifier was reused before completion.

Compile result:

```text
compile_exit_code=0
```

Observed output:

```text
incoming_qos2_exchanges=65536 subscriber_publish_writes=65536 first_subscriber_publish_id=1 repeated_first_id_at_subscriber_publish=65536 last_subscriber_publish_id=1 publisher_response_writes=131072 next_packet_id=2
exit_code=2
```

Interpretation: the probe drives 65,536 normal incoming QoS2 publishes through broker live fan-out. The subscriber never sends `PUBCOMP` for the broker's outgoing QoS2 publishes. The first outgoing subscriber `PUBLISH` uses Packet Identifier `1`; the 65,536th outgoing subscriber `PUBLISH` also uses Packet Identifier `1`. The probe returns `2` when this forbidden reuse is observed.

## Impact

A subscriber can receive two different QoS1/QoS2 Server-to-Client `PUBLISH` packets with the same Packet Identifier before completing the first acknowledgement flow. The subscriber's `PUBACK` or `PUBREC` / `PUBCOMP` correlation becomes ambiguous, breaking QoS delivery state.

## Fix Direction

Give static-memory live fan-out the same per-subscriber outbound in-flight tracking used by the dynamic `out_q` path, or route static live QoS1/QoS2 deliveries through a bounded per-subscriber queue. The Packet Identifier should be selected from ids not currently outstanding for that subscriber and released only after the matching `PUBACK` or `PUBCOMP`.
