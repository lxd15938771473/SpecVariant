# PUBLISH retransmission keeps DUP clear after partial blocking write failure

## Summary

wolfMQTT broker correctly retransmits unacknowledged PUBLISH messages with `DUP=1` during ordinary `CleanSession=0` session recovery. However, in the blocking write path, if a PUBLISH writes some bytes and then the same write operation returns a network error, the queue entry remains `QUEUED` with `retransmit_dup=0`. When the subscriber reconnects, the broker retransmits the same QoS>0 PUBLISH with `DUP=0`.

## Standard Requirement

Reference: [MQTT v3.1.1 specification, Sections 3.3.1.1, 4.3.2, and 4.4](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html).

The DUP rules state that `DUP=0` indicates the sender's first attempt to send a PUBLISH Packet, and `DUP=1` is required when a Client or Server attempts to retransmit a PUBLISH Packet. For server-to-subscriber forwarding, the outgoing DUP value is determined only by whether that outgoing PUBLISH is a retransmission.

For `CleanSession=0` reconnect, Client and Server must retransmit unacknowledged QoS>0 PUBLISH and PUBREL packets using the original Packet Identifier.

## Relevant Source Code

When the broker encodes an outgoing PUBLISH from the queue, DUP comes entirely from `cur->retransmit_dup`:

```c
out_pub.qos        = cur->qos;
out_pub.packet_id  = cur->packet_id;
out_pub.duplicate  = cur->retransmit_dup;
```

Only the `MQTT_CODE_CONTINUE` path marks a partially written QoS>0 PUBLISH as a retransmission:

```c
wr_rc = MqttPacket_Write(&bc->client, bc->tx_buf, enc_rc);
if (wr_rc == MQTT_CODE_CONTINUE) {
    bc->out_q_pending_len = enc_rc;
    if (cur->qos > MQTT_QOS_0 && bc->client.write.pos > 0) {
        cur->retransmit_dup = 1;
    }
    return wr_rc;
}
```

The write-error branch returns the network error but does not set `retransmit_dup=1` when `write.pos > 0`:

```c
if (wr_rc != enc_rc) {
    return (wr_rc < 0) ? wr_rc : MQTT_CODE_ERROR_NETWORK;
}
```

The abnormal-disconnect path moves the client's outbound queue into an orphan session. On reconnect, it fixes entries already marked `PUBLISH_SENT`, but it does not correct a partially written entry that still remained `QUEUED`.

## Runtime Evidence

A normal session-recovery control was compiled and run:

```text
initial_forward_publish_flag=0x32
sub1_bytes: 20 02 00 00 90 03 00 01 01 32 06 00 01 74 00 01 58
redeliver_publish_flag=0x3A
sub2_bytes: 20 02 01 00 3A 06 00 01 74 00 01 58
```

Observed result: `0x32` is QoS1 with `DUP=0`; `0x3A` is QoS1 with `DUP=1`, proving the ordinary `PUBLISH_SENT` recovery path works.

A blocking-write partial-failure reproducer was then compiled and run:

```text
partial_initial_publish_flag=0x32
sub1_bytes: 20 02 00 00 90 03 00 01 01 32 06 00
redeliver_after_partial_error_flag=0x32
sub2_bytes: 20 02 01 00 32 06 00 01 74 00 01 58
```

Observed result: `sub1_bytes` ends with `32 06 00`, showing the first PUBLISH was partially written. `sub2_bytes` begins with `20 02 01 00`, showing the old session was resumed, but the retransmitted PUBLISH still uses `0x32` (`DUP=0`) instead of the required `0x3A`.

## Decision Reason

The standard requires retransmitted PUBLISH packets to set `DUP=1`. The blocking-write "partial success then failure" path does not mark an already attempted QoS>0 PUBLISH as a retransmission, and the runtime result confirms the recovered session retransmits it with `DUP=0`.

## Fix Direction

When a blocking write has advanced `client.write.pos` for a QoS>0 queued PUBLISH and then returns an error, mark that queue entry for retransmission before preserving it for session recovery.
