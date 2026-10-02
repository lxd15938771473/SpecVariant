# Client reuses an unacknowledged UNSUBSCRIBE Packet Identifier

## Summary

Extracted rule: each time a client sends a new `UNSUBSCRIBE`, it must assign a currently unused Packet Identifier.

wolfMQTT can write a second new `UNSUBSCRIBE` that reuses the Packet Identifier of an earlier `UNSUBSCRIBE` before the corresponding `UNSUBACK` has been received.

## Standard Requirement

Official standard: [MQTT Version 3.1.1](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html)

MQTT 3.1.1 Section 2.3.1, Packet Identifier:

```text
Each time a Client sends a new packet of one of these types it MUST assign it a currently unused Packet Identifier.
```

This rule applies to `SUBSCRIBE`, `UNSUBSCRIBE`, and QoS > 0 `PUBLISH`. For `UNSUBSCRIBE`, the Packet Identifier becomes available for reuse only after the client has processed the corresponding `UNSUBACK`. Reusing the original Packet Identifier is allowed only when retransmitting the same Control Packet.

## Relevant Source Code

`MqttEncode_Unsubscribe` rejects only Packet Identifier `0`, then directly encodes the caller-provided `unsubscribe->packet_id`:

```c
/* src/mqtt_packet.c:3325-3328 */
if (unsubscribe->packet_id == 0) {
    return MQTT_TRACE_ERROR(MQTT_CODE_ERROR_PACKET_ID);
}

/* src/mqtt_packet.c:3400-3401 */
tx_payload += MqttEncode_Num(&tx_buf[header_len], unsubscribe->packet_id);
```

`MqttClient_Unsubscribe` sends the packet and waits for an `UNSUBACK` with the same Packet Identifier. The pending response list is compiled only under `WOLFMQTT_MULTITHREAD`:

```c
/* src/mqtt_client.c:3461-3468 */
#ifdef WOLFMQTT_MULTITHREAD
rc = MqttClient_RespList_Add(client,
    MQTT_PACKET_TYPE_UNSUBSCRIBE_ACK, unsubscribe->packet_id,
    &unsubscribe->pendResp, &unsubscribe->ack);
#endif

/* src/mqtt_client.c:3481-3505 */
rc = MqttPacket_Write(client, client->tx_buf, xfer);
rc = MqttClient_WaitType(client, &unsubscribe->ack,
    MQTT_PACKET_TYPE_UNSUBSCRIBE_ACK, unsubscribe->packet_id,
    client->cmd_timeout_ms, NULL);
```

The default build options leave nonblocking and multithread support disabled:

```cmake
/* CMakeLists.txt:101-105, 156-160 */
add_option("WOLFMQTT_NONBLOCK" "Enable non-blocking support" "no" "yes;no")
add_option(WOLFMQTT_MT "Enable multiple thread support" "no" "yes;no")
```

Even when `WOLFMQTT_MULTITHREAD` is enabled, duplicate detection is tied to the pending response list, and the entry is removed after the wait finishes:

```c
/* src/mqtt_client.c:595-598 */
if (packet_id != 0 && tmpResp->packet_id == packet_id) {
    return MQTT_TRACE_ERROR(MQTT_CODE_ERROR_PACKET_ID);
}

/* src/mqtt_client.c:3511-3515 */
#ifdef WOLFMQTT_MULTITHREAD
MqttClient_RespList_Remove(client, &unsubscribe->pendResp);
#endif
```

## Implementation Behavior

In the default synchronous path, the caller supplies `MqttUnsubscribe.packet_id`. After the first `UNSUBSCRIBE` is written and the wait for `UNSUBACK` times out, the connection remains marked as connected. The client has no global outbound in-flight Packet Identifier table that keeps the identifier unavailable until the `UNSUBACK` is processed. A second, different `UNSUBSCRIBE` can therefore reuse the same Packet Identifier and still be written to the network layer.

## Inconsistency Reason

The standard requires a new `UNSUBSCRIBE` to use a currently unused Packet Identifier. The original identifier cannot be reused until the corresponding `UNSUBACK` is processed. wolfMQTT checks only that the identifier is nonzero in the default path, so it allows two different new `UNSUBSCRIBE` packets to share the same identifier while the first one remains unacknowledged.

## Runtime Evidence

A focused client probe was compiled and run. It sent two different `UNSUBSCRIBE` requests on the same connected client while withholding `UNSUBACK` for the first, then recorded the packet type, flags, topic, and Packet Identifier for each write.

```text
build_exit_code=0
rc_init=0 rc_net=0 rc1=-7 rc2=-7
topic0=sensor/temp topic1=sensor/humidity
connect_count=1 disconnect_count=0 read_count=2 write_count=2
write0_type=10 write0_flags=0x2 write0_id=42 write0_len=17
write1_type=10 write1_flags=0x2 write1_id=42 write1_len=21
flags_after=0x00000001 connected_after=1
RESULT=reproduced_reused_unacknowledged_unsubscribe_packet_id
run_exit_code=1
```

The two topics are different, so the second packet is not a retransmission of the same Control Packet. Both writes are `UNSUBSCRIBE` packets and both use Packet Identifier `42`. The probe uses `run_exit_code=1` as the expected issue-found marker, not as a crash indicator.

## Impact

A broker can observe two different in-flight `UNSUBSCRIBE` operations using the same Packet Identifier. The later `UNSUBACK` association becomes ambiguous, and the client's Packet Identifier lifecycle no longer follows MQTT 3.1.1.

## Fix Direction

Track outbound in-flight Packet Identifiers on the client side for all builds. For new `SUBSCRIBE`, `UNSUBSCRIBE`, and QoS > 0 `PUBLISH` packets, reject an identifier that is still in use before encoding or writing the packet. Release it only after the corresponding `SUBACK`, `UNSUBACK`, `PUBACK`, or `PUBCOMP` has been processed, or after the connection/session state is intentionally cleared.
