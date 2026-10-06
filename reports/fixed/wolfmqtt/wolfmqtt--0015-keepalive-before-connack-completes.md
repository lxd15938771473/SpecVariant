# Keepalive Can Close Accepted CONNECT Before CONNACK Completes

## Summary

For an accepted MQTT 3.1.1 CONNECT, wolfMQTT delays reads and queued message delivery while CONNACK is partially written, but keepalive timeout checking still runs. With `WOLFMQTT_NONBLOCK`, a slow write can close the accepted connection after only byte `20` of `20 02 00 00` has been sent.

## Standard Requirement

- Official standard: [MQTT Version 3.1.1](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html)
- Section: `3.1.4 Response`

Normative excerpt:

```text
3.The Server MUST acknowledge the CONNECT Packet ...
4.Start message delivery and keep alive monitoring.
```

Interpretation: after successful validation, the server must complete the accepted CONNACK step before starting message delivery and keepalive monitoring.

## Relevant Source Code

`implementions/wolfMQTT-master/src/mqtt_broker.c:6253-6255` arms keepalive state during CONNECT handling:

```c
bc->protocol_level = mc.protocol_level;
bc->keep_alive_sec = mc.keep_alive_sec;
bc->last_rx = WOLFMQTT_BROKER_GET_TIME_S();
```

`implementions/wolfMQTT-master/src/mqtt_broker.c:6927-6968` writes CONNACK and records a pending accepted CONNACK after a partial write:

```c
rc = MqttPacket_Write(&bc->client, bc->tx_buf, rc);
...
if (rc == MQTT_CODE_CONTINUE) {
    bc->connack_pending_len = ack_len;
}
```

`implementions/wolfMQTT-master/src/mqtt_broker.c:8141-8175` blocks further reads while CONNACK is pending:

```c
if (bc->connack_pending_len != 0) {
    rc = BrokerClient_ResumeConnAck(broker, bc);
}
else {
    rc = MqttPacket_Read(&bc->client, bc->rx_buf, BROKER_CLIENT_RX_SZ(bc), 0);
}
```

`implementions/wolfMQTT-master/src/mqtt_broker.c:8511-8541` still applies keepalive timeout while CONNACK may be pending:

```c
if (bc->keep_alive_sec > 0) {
    ...
    if (now >= bc->last_rx && (now - bc->last_rx) >= deadline) {
        BrokerClient_PublishWill(broker, bc);
        BrokerClient_Remove(broker, bc);
        return 0;
    }
}
```

## Implementation Behavior

Message delivery is correctly gated: `BrokerClient_DrainOutQueue` returns when `connack_pending_len != 0` (`mqtt_broker.c:1985-1987`), and the process loop does not read more packets until CONNACK finishes.

Keepalive is not gated the same way. Once CONNECT is accepted, `keep_alive_sec` is active and the timeout check can remove the client while `connack_pending_len` is nonzero.

## Inconsistency Reason

The standard orders keepalive monitoring after the accepted CONNACK step. wolfMQTT can enforce keepalive before the accepted CONNACK packet is fully written, closing the handshake mid-CONNACK.

## Runtime Evidence

A focused broker probe was compiled and run. It simulated an accepted CONNECT in a nonblocking write configuration, forced the accepted CONNACK to remain partially written, advanced broker time across the keepalive deadline, and compared that behavior with a full-write control and a resume-before-deadline control.

Build result:

```text
build-ok
```

Run output:

```text
full_write_control: now=0 out_len=4 closed=0 write_calls=1 bytes=20 02 00 00
pending_after_connect: now=0 out_len=1 closed=0 write_calls=2 bytes=20
pending_before_deadline: now=1 out_len=1 closed=0 write_calls=3 bytes=20
pending_at_deadline: now=2 out_len=1 closed=1 write_calls=4 bytes=20
probe result: issue reproduced; keepalive closed while accepted CONNACK was still pending
resume_before_deadline_control: now=1 out_len=4 closed=0 write_calls=2 bytes=20 02 00 00
```

The probe used Keep Alive `1`; wolfMQTT's deadline calculation is 2 seconds. At `now=2`, only byte `20` of the accepted CONNACK had been sent, yet the connection was closed.

## Impact

Affected configurations are `WOLFMQTT_NONBLOCK` or custom network callbacks that can leave the accepted CONNACK partially written past the keepalive deadline. Clients can observe a truncated accepted CONNACK instead of a completed connection acknowledgment.

## Fix Direction

Do not enforce accepted-connection keepalive timeout while `connack_pending_len != 0`, or arm the keepalive baseline only after `BrokerClient_ResumeConnAck` completes the accepted CONNACK. Keep the existing guard that delays reads and queued message delivery until the CONNACK finishes.
