# Broker publish-response protocol violations with Packet Identifier 0 do not close the connection

## Problem Description

MQTT 3.1.1 requires an endpoint to close the Network Connection when it receives a Control Packet that causes a protocol violation, unless another rule states otherwise. On the broker side, wolfMQTT decodes publish-response packets such as `PUBACK`, `PUBREC`, `PUBREL`, and `PUBCOMP` without rejecting Packet Identifier `0`. The broker then treats the zero identifier as unmatched, spurious, or idempotent and continues processing instead of closing the offending connection.

## Standard Requirement

Official standard: [MQTT Version 3.1.1](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html), Section 4.8 Handling errors.

```text
[MQTT-4.8.0-1] Unless stated otherwise, if either the Server or Client encounters a protocol violation, it MUST close the Network Connection on which it received that Control Packet which caused the protocol violation.
```

For this report, the relevant endpoint is the server. A received publish-response Control Packet with an invalid Packet Identifier is a protocol violation; the broker should close the connection that delivered that packet.

## Relevant Source Code

`implementions/wolfMQTT-master/src/mqtt_broker.c:1485-1492` shows the broker transport close path:

```c
static int BrokerNetDisconnect(void* context)
{
    BrokerClient* bc = (BrokerClient*)context;
    if (bc != NULL && bc->broker != NULL &&
        bc->sock != BROKER_SOCKET_INVALID) {
        WBLOG_INFO(bc->broker, "broker: disconnect sock=%d", (int)bc->sock);
        bc->broker->net.close(bc->broker->net.ctx, bc->sock);
        bc->sock = BROKER_SOCKET_INVALID;
```

`implementions/wolfMQTT-master/src/mqtt_broker.c:7979-8012` defines abnormal close and the broker fatal-error classifier:

```c
static void BrokerClient_AbnormalClose(MqttBroker* broker, BrokerClient* bc)
{
    BrokerClient_PublishWill(broker, bc);
    if (bc->session_expiry_sec == 0) {
        BrokerSubs_EndClientSession(broker, bc);
    }
    else {
        BrokerSubs_OrphanClient(broker, bc);
    }
    BrokerClient_Remove(broker, bc);
}

static int BrokerRcIsFatal(int rc)
{
    return (rc == MQTT_CODE_ERROR_MALFORMED_DATA ||
            rc == MQTT_CODE_ERROR_PACKET_TYPE ||
            rc == MQTT_CODE_ERROR_PACKET_ID ||
            rc == MQTT_CODE_ERROR_PROPERTY ||
            rc == MQTT_CODE_ERROR_PROPERTY_MISMATCH ||
            rc == MQTT_CODE_ERROR_MEMORY ||
            rc == MQTT_CODE_ERROR_OUT_OF_BUFFER);
```

The broker already has a fatal path for `MQTT_CODE_ERROR_PACKET_ID`. The problem is that the publish-response decoder does not return that error for Packet Identifier `0`.

`implementions/wolfMQTT-master/src/mqtt_packet.c:2736-2825` decodes the publish response and reads the Packet Identifier, but it does not reject the zero value:

```c
int MqttDecode_PublishResp(byte* rx_buf, int rx_buf_len, byte type,
    MqttPublishResp *publish_resp)
{
    ...
    if (publish_resp) {
        int tmp;
        tmp = MqttDecode_Num(rx_payload, &publish_resp->packet_id,
                (word32)(rx_buf_len - (rx_payload - rx_buf)));
        if (tmp < 0) {
            return tmp;
        }
        rx_payload += tmp;
```

`implementions/wolfMQTT-master/src/mqtt_broker.c:2188-2257` treats Packet Identifier `0` as no match for outbound QoS 1 state:

```c
static BrokerOutPub* BrokerClient_FindOutPub(BrokerClient* bc,
    word16 packet_id, byte expected_state, BrokerOutPub** out_prev)
{
    BrokerOutPub* prev = NULL;
    BrokerOutPub* cur;

    if (bc == NULL || packet_id == 0) {
        return NULL;
    }
    ...
}

static void BrokerClient_OnPubAck(BrokerClient* bc, word16 packet_id)
{
    ...
    e = BrokerClient_FindOutPub(bc, packet_id, BROKER_OUTQ_PUBLISH_SENT,
            &prev);
    if (e == NULL) {
        WBLOG_DBG(bc->broker,
            "broker: spurious PUBACK sock=%d packet_id=%u",
            (int)bc->sock, (unsigned)packet_id);
        return;
    }
```

`implementions/wolfMQTT-master/src/mqtt_broker.c:2327-2341` similarly treats unmatched `PUBCOMP` as spurious:

```c
static void BrokerClient_OnPubComp(BrokerClient* bc, word16 packet_id)
{
    BrokerOutPub* prev = NULL;
    BrokerOutPub* e;

    if (bc == NULL) {
        return;
    }
    e = BrokerClient_FindOutPub(bc, packet_id, BROKER_OUTQ_PUBREL_SENT,
            &prev);
    if (e == NULL) {
        WBLOG_DBG(bc->broker,
            "broker: spurious PUBCOMP sock=%d packet_id=%u",
            (int)bc->sock, (unsigned)packet_id);
        return;
```

`implementions/wolfMQTT-master/src/mqtt_broker.c:8273-8357` dispatches decoded `PUBACK` and `PUBCOMP` values to those handlers and closes only if the decoder returned a fatal error:

```c
ack_rc = MqttDecode_PublishResp(bc->rx_buf, rc,
        MQTT_PACKET_TYPE_PUBLISH_ACK, &ack_resp);
if (ack_rc >= 0) {
#ifdef WOLFMQTT_STATIC_MEMORY
    BrokerStaticOrphan_OnPubAck(broker, bc,
        ack_resp.packet_id);
#else
    BrokerClient_OnPubAck(bc, ack_resp.packet_id);
#endif
}
...
if (BrokerRcIsFatal(ack_rc)) {
    BrokerClient_AbnormalClose(broker, bc);
    return 0;
}
```

Because Packet Identifier `0` is decoded successfully, `BrokerRcIsFatal` is not triggered.

`implementions/wolfMQTT-master/src/mqtt_broker.c:7852-7897` decodes `PUBREL` and can reply with `PUBCOMP` using the decoded Packet Identifier:

```c
rc = MqttDecode_PublishResp(bc->rx_buf, rx_len,
        MQTT_PACKET_TYPE_PUBLISH_REL, &resp);
if (rc < 0) {
    WBLOG_ERR(bc->broker, "broker: PUBLISH_REL decode failed rc=%d", rc);
    return rc;
}
...
rc = MqttEncode_PublishResp(bc->tx_buf, BROKER_CLIENT_TX_SZ(bc),
        MQTT_PACKET_TYPE_PUBLISH_COMP, &resp);
```

The same missing zero-id rejection therefore affects multiple publish-response packet types.

## Evidence

A source-backed probe was run against the wolfMQTT broker tree. The positive control verified that the intended target source root was present. The reproducer then checked the publish-response decoder and broker dispatch paths for the root requirement and confirmed the relevant static source excerpts:

```text
positive-control: target source root verified
reproducer: the requirement verified 12 static source excerpts
status: passed
exit_code: 0
```

The verified excerpts show that the broker has an abnormal-close path and treats `MQTT_CODE_ERROR_PACKET_ID` as fatal, but `MqttDecode_PublishResp` accepts Packet Identifier `0` and passes it to handlers that treat it as spurious or unmatched. The observed implementation behavior is therefore not an infrastructure artifact: the close path exists, but this invalid publish-response input does not reach it.

## Inconsistency Reason

The MQTT requirement says the server must close the connection that delivered a Control Packet causing a protocol violation. A publish-response packet with Packet Identifier `0` is such a violation. wolfMQTT instead accepts the zero identifier at decode time and lets broker handlers ignore it as unmatched state, so the offending connection remains open.

## Impact

A peer can send invalid publish-response packets with Packet Identifier `0` without triggering the broker's protocol-violation close behavior. This weakens MQTT error handling and can leave invalid peer state on a connection that should have been terminated.

## Fix Direction

Update `MqttDecode_PublishResp` to reject Packet Identifier `0` for MQTT 3.1.1 `PUBACK`, `PUBREC`, `PUBREL`, and `PUBCOMP` by returning `MQTT_CODE_ERROR_PACKET_ID`. The existing broker fatal-error path can then close the offending connection. Add regression coverage for each publish-response type with Packet Identifier `0`.
