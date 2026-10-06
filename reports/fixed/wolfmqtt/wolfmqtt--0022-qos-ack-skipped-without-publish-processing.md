# Client skips QoS ACK when application does not process received PUBLISH

## Standard Requirement

MQTT 3.1.1 section 4.5, Message receipt:

```text
[MQTT-4.5.0-2] The Client MUST acknowledge any Publish Packet it receives
according to the applicable QoS rules regardless of whether it elects to process
the Application Message that it contains.
```

This applies to a Client receiving a PUBLISH from the Server. For QoS 1 the
Client must send PUBACK; for QoS 2 it must send PUBREC, even if the application
chooses not to process the message.

## Relevant Source Code

`implementions/wolfMQTT-master/src/mqtt_client.c:2697-2700`

```c
rc = client->msg_cb(client, publish, publish->buffer_new,
                    msg_done);
if (rc != MQTT_CODE_SUCCESS) {
    return rc;
};
```

The message callback result is returned immediately when it is not success.

`implementions/wolfMQTT-master/src/mqtt_client.c:2759-2766`

```c
/* No message callback registered to deliver this incoming PUBLISH. The
 * payload was drained above to keep the stream in sync, but the application
 * never saw it. Return a distinct error instead of success so the caller is
 * notified and, for QoS 1/2, MqttClient_HandlePacket does not falsely ACK
 * the message as delivered. */
if (rc == MQTT_CODE_SUCCESS && client->msg_cb == NULL) {
    rc = MQTT_TRACE_ERROR(MQTT_CODE_ERROR_CALLBACK);
}
```

When no callback is registered, the payload is drained but the function returns
an error specifically to avoid ACKing the message.

`implementions/wolfMQTT-master/src/mqtt_client.c:1173-1216`

```c
rc = MqttClient_Publish_ReadPayload(client, publish, timeout_ms);

if (rc == MQTT_CODE_CONTINUE) {
    break;
}

if (rc < 0) {
    break;
}

if (packet_qos == MQTT_QOS_0) {
    break;
}

resp->packet_type = (packet_qos == MQTT_QOS_1) ?
    MQTT_PACKET_TYPE_PUBLISH_ACK :
    MQTT_PACKET_TYPE_PUBLISH_REC;
resp->packet_id = packet_id;
```

PUBACK/PUBREC is only prepared after `MqttClient_Publish_ReadPayload()` returns
success. A callback error or missing callback exits before ACK setup.

`implementions/wolfMQTT-master/src/mqtt_client.c:1867-1991`

```c
if (MqttIsPubRespPacket(resp.packet_type)) {
    XMEMCPY(&client->packetAck, &resp, sizeof(MqttPublishResp));
    mms_stat->read = MQTT_MSG_ACK;
    mms_stat->ack = MQTT_MSG_WAIT;
}

rc = MqttEncode_PublishResp(client->tx_buf, client->tx_buf_len,
    client->packetAck.packet_type, &client->packetAck);

rc = MqttPacket_Write(client, client->tx_buf, xfer);
```

The actual network write occurs only if `resp.packet_type` was set to a publish
response packet. In the failing paths above, it remains unset.

## Runtime Evidence

A focused client harness was compiled and run. It delivered QoS 1 and QoS 2 inbound `PUBLISH` packets in three cases: callback success, callback rejection, and no callback registered. The harness recorded whether the real ACK write path produced `PUBACK` or `PUBREC`.

Run output:

```text
qos1_callback_success wait_rc=0 callback_calls=1 write_calls=1 written_len=4 written=40 02 12 34
qos1_callback_reject wait_rc=-13 callback_calls=1 write_calls=0 written_len=0 written=
qos1_no_callback wait_rc=-13 callback_calls=0 write_calls=0 written_len=0 written=
qos2_callback_success wait_rc=0 callback_calls=1 write_calls=1 written_len=4 written=50 02 12 34
qos2_callback_reject wait_rc=-13 callback_calls=1 write_calls=0 written_len=0 written=
qos2_no_callback wait_rc=-13 callback_calls=0 write_calls=0 written_len=0 written=
```

The positive controls prove the harness reaches wolfMQTT's real ACK write path:
QoS 1 writes PUBACK `40 02 12 34`, and QoS 2 writes PUBREC `50 02 12 34`.
When the callback rejects the message, or when no callback is registered, there
are zero write calls, so the Client does not acknowledge the received QoS 1/2
PUBLISH.

## Impact

A Server can keep the QoS 1/2 PUBLISH unacknowledged and retransmit it because
the Client never sends the required PUBACK/PUBREC. This violates
`MQTT-4.5.0-2` when the application declines message processing.

## Fix Direction

Drain the received PUBLISH payload, then prepare and send the QoS ACK whenever
the packet itself is valid and QoS is 1 or 2. Application callback errors should
still be reported to the caller, but they should not suppress the protocol ACK
required by `MQTT-4.5.0-2`.
