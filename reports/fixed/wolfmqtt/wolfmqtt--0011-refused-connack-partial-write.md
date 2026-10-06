# Refused CONNACK Partial Write Closes Before Return Code Delivery

## Summary

wolfMQTT does choose a non-zero MQTT 3.1.1 CONNACK return code for authentication failure. In `WOLFMQTT_NONBLOCK` dynamic-memory broker builds, however, a short write of that refused CONNACK is not resumed before the broker closes the connection. The client can receive only the first byte of CONNACK and never receive the non-zero return code.

## Standard Requirement

Official standard: [MQTT Version 3.1.1](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html), Section 3.1.4 "Response"

```text
If any of these checks fail, it SHOULD send an appropriate CONNACK response with a non-zero return code ... and it MUST close the Network Connection.
```

Meaning: after an authentication/authorization/further-restriction failure, the server is expected to send a refused CONNACK and then close. Closing is required; silent or truncated refusal is not the intended response unless there is a justified exception.

## Relevant Source Code

`implementions/wolfMQTT-master/src/mqtt_broker.c:6491-6510`

```c
if (!auth_ok) {
    ack.return_code = MQTT_CONNECT_ACK_CODE_REFUSED_BAD_USER_PWD;
    goto send_connack;
}
```

Authentication failure maps to MQTT 3.1.1 CONNACK return code `0x04`.

`implementions/wolfMQTT-master/src/mqtt_broker.c:6935-6968`

```c
rc = MqttEncode_ConnectAck(bc->tx_buf, BROKER_CLIENT_TX_SZ(bc), &ack);
if (rc > 0) {
    ack_len = rc;
    rc = MqttPacket_Write(&bc->client, bc->tx_buf, rc);
}

if (ack.return_code != MQTT_CONNECT_ACK_CODE_ACCEPTED) {
    return 0;
}

if (rc == MQTT_CODE_CONTINUE) {
    bc->connack_pending_len = ack_len;
}
```

The pending-write path is after the refused-CONNACK `return 0`, so it only covers accepted CONNACK writes.

`implementions/wolfMQTT-master/src/mqtt_broker.c:8237-8259`

```c
int c_rc = BrokerHandle_Connect(bc, rc, broker);
if (c_rc <= 0 && bc->connack_pending_len == 0) {
    BrokerClient_Remove(broker, bc);
    return 0;
}
```

The caller closes the client when the refused path returns `0` and no pending CONNACK is recorded.

`implementions/wolfMQTT-master/src/mqtt_socket.c:192-205`

```c
rc = MqttSocket_WriteDo(client, &buf[client->write.pos],
    buf_len - client->write.pos, timeout_ms);
if (rc >= 0) {
    client->write.pos += rc;
    if (client->write.pos < buf_len) {
        rc = MQTT_CODE_CONTINUE;
    }
}
```

In nonblocking mode, a positive short write returns `MQTT_CODE_CONTINUE` with bytes still pending.

## Implementation Behavior

For bad credentials, the broker encodes `20 02 00 04`, where `0x04` is `MQTT_CONNECT_ACK_CODE_REFUSED_BAD_USER_PWD`. If the write callback accepts only one byte, `MqttPacket_Write` returns `MQTT_CODE_CONTINUE`. Because the refused path returns before setting `connack_pending_len`, `BrokerClient_Process` removes the client immediately and no later step resumes the remaining bytes.

## Inconsistency Reason

The implementation satisfies the return-code selection part, but not reliable delivery in nonblocking partial-write cases. The standard expects a refused CONNACK with a non-zero return code before close; the observed client-visible result is only `20`, not the full `20 02 00 04`.

## Runtime Evidence

A focused broker reproducer was compiled and run with a write callback that accepts only the first byte of the refused CONNACK. The broker receives a CONNECT that fails authentication, chooses the expected non-zero CONNACK return code, attempts the write, and then removes the client before the remaining bytes are resumed.

Observed result:

```text
listen=1 accept=6 read=2 write=1 close=1
closed=1 out_len=1 bytes=20
expected_full=20 02 00 04
REPRODUCED: refused CONNACK was not fully delivered before close
```

## Impact

In `WOLFMQTT_NONBLOCK` broker deployments, clients rejected by authentication or similar CONNECT checks can see a truncated CONNACK and cannot read the refusal code.

## Fix Direction

Handle `MQTT_CODE_CONTINUE` for refused CONNACKs before closing: record the pending CONNACK length, resume it in later broker steps, and close only after the full refused CONNACK is written or a hard write error occurs.
