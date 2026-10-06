# PINGRESP write failure does not publish Will

## Summary

- Verdict: `issue_found`
- Requirement: `req-0666d7a7b40b4b187e44`
- Root cause: `root-req-0666d7a7b40b4b187e44`
- Scope: a connected client has a Will; broker receives `PINGREQ`; writing `PINGRESP` fails with a network I/O error.

The broker correctly publishes Will on read-side network errors, but the `PINGREQ` response write error is ignored, so the client remains connected and its Will is not published.

## Standard Requirement

Source: [MQTT v3.0/v3.1 specification, CONNECT Will behavior](https://public.dhe.ibm.com/software/dw/webservices/ws-mqtt/mqtt-v3r1.html).

```text
The Will message defines that a message is published on behalf of the client by the broker when either an I/O error is encountered by the broker during communication with the client, or the client fails to communicate within the Keep Alive timer schedule.
```

Meaning: after the broker has stored a client's Will, any broker-side communication I/O error for that client must cause the broker to publish that Will on behalf of the client.

## Relevant Source Code

`src/mqtt_broker.c:5973-5981`

```c
static int BrokerSend_PingResp(BrokerClient* bc)
{
    if (bc == NULL) {
        return MQTT_CODE_ERROR_BAD_ARG;
    }
    WBLOG_DBG(bc->broker, "broker: PINGREQ -> PINGRESP sock=%d", (int)bc->sock);
    bc->tx_buf[0] = MQTT_PACKET_TYPE_SET(MQTT_PACKET_TYPE_PING_RESP);
    bc->tx_buf[1] = 0;
    return BrokerClient_WriteDirect(bc, 2);
}
```

`BrokerSend_PingResp` returns the network write result from `BrokerClient_WriteDirect`.

`src/mqtt_broker.c:8379-8388`

```c
case MQTT_PACKET_TYPE_PING_REQ:
    if (bc->client.packet.remain_len != 0) {
        BrokerClient_AbnormalClose(broker, bc);
        return 0;
    }
    (void)BrokerSend_PingResp(bc);
    break;
```

The `PINGREQ` handler discards that result. If the `PINGRESP` write returns `MQTT_CODE_ERROR_NETWORK`, no abnormal close is performed.

`src/mqtt_broker.c:7980-7989`

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
```

This is the path that should be reached after the write I/O error.

## Implementation Behavior

For read errors, `BrokerClient_Process` calls `BrokerClient_AbnormalClose`, so Will publication works. For the `PINGREQ` write path, the broker attempts to send `PINGRESP`, ignores the failed write result, and continues processing with `bc->in_use=1` and `bc->has_will=1`.

## Inconsistency Reason

The standard requires Will publication when the broker encounters an I/O error while communicating with a Will-bearing client. A failed `PINGRESP` write is such an I/O error, but the implementation drops the error and never calls `BrokerClient_AbnormalClose`, so no Will `PUBLISH` is emitted.

## Runtime Evidence

A focused Will-publication probe was compiled and run in two scenarios. The positive control forced a broker-side read I/O error for a connected Will-bearing client. The reproducer read a valid `PINGREQ`, then forced the `PINGRESP` write to return `MQTT_CODE_ERROR_NETWORK`.

Positive control, forced read I/O error:

```text
process_rc=0 read_error_calls=1 sub_writes=1 sub_first=0x30 topic_seen=1 payload_seen=1 will_in_use=0 will_has_will=0 close_calls=1 will_writes=0
```

Reproducer, `PINGREQ` is read and `PINGRESP` write returns `MQTT_CODE_ERROR_NETWORK`:

```text
process_rc=1 will_read_calls=1 will_write_errors=1 sub_writes=0 sub_first=0x00 topic_seen=0 payload_seen=0 will_in_use=1 will_has_will=1 close_calls=0
```

The read-error control publishes a Will `PUBLISH` (`sub_first=0x30`, topic and payload seen). The write-error reproducer emits no Will, does not close the client, and leaves the Will pending.

## Impact

Subscribers can miss a client's Will when the first detected communication failure is a failed broker-to-client `PINGRESP` write.

## Fix Direction

Check the return value of `BrokerSend_PingResp`; if it is a terminal write failure or short write, call `BrokerClient_AbnormalClose(broker, bc)` and stop processing that client.
