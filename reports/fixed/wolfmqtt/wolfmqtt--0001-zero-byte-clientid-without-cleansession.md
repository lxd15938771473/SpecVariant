# Zero-byte ClientId can be sent without CleanSession

## Summary

wolfMQTT's MQTT 3.1.1 client encoder accepts `client_id=""` with `clean_session=0` and `MqttClient_Connect` can write that CONNECT to the network. MQTT 3.1.1 requires a zero-byte ClientId to be paired with `CleanSession=1`.

## Standard Requirement

- MQTT 3.1.1: <https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html>
- Section: `3.1.3.1 Client Identifier`

```text
If the Client supplies a zero-byte ClientId, the Client MUST also set CleanSession to 1.
```

The next rule requires the Server to reject `zero-byte ClientId + CleanSession=0`, but that does not remove the Client's own MUST NOT send this combination.

## Relevant Source Code

`wolfmqtt/mqtt_packet.h:470-472` exposes `clean_session` and `client_id` as independent CONNECT inputs:

```c
word16      keep_alive_sec;
byte        clean_session;
const char *client_id;
```

`src/mqtt_packet.c:1432-1498` validates null pointer, protocol level, length, and UTF-8, but has no check for `client_id` length zero with `clean_session=0`:

```c
if (tx_buf == NULL || mc_connect == NULL || mc_connect->client_id == NULL) {
    return MQTT_TRACE_ERROR(MQTT_CODE_ERROR_BAD_ARG);
}

size_t str_len = XSTRLEN(mc_connect->client_id);
if (str_len > (size_t)0xFFFF) {
    return MQTT_TRACE_ERROR(MQTT_CODE_ERROR_BAD_ARG);
}
remain_len += (int)str_len + MQTT_DATA_LEN_SIZE;
```

`src/mqtt_packet.c:1608-1652` sets the CleanSession bit only if requested, then always encodes the ClientId string:

```c
if (mc_connect->clean_session) {
    packet.flags |= MQTT_CONNECT_FLAG_CLEAN_SESSION;
}

tx_payload += MqttEncode_String(tx_payload, mc_connect->client_id);
```

`src/mqtt_client.c:2406-2456` shows the public client path uses this encoder and then writes the encoded CONNECT:

```c
rc = MqttEncode_Connect(client->tx_buf, client->tx_buf_len, mc_connect);
client->write.len = rc;
rc = MqttPacket_Write(client, client->tx_buf, xfer);
```

The broker path is not the problem: `src/mqtt_broker.c:6237-6250` rejects v3.1.1 `id_len == 0 && !mc.clean_session` with `MQTT_CONNECT_ACK_CODE_REFUSED_ID`.

## Implementation Behavior

For MQTT 3.1.1, `MqttEncode_Connect` treats an empty string as a valid ClientId field length and leaves the CleanSession flag clear when `clean_session=0`. `MqttClient_Connect` does not add another guard before writing the encoded packet.

## Inconsistency Reason

The standard places a direct requirement on the Client: a zero-byte ClientId must also set CleanSession to 1. wolfMQTT can encode and send a CONNECT where the ClientId length is zero and the CleanSession bit is zero, so the client path can produce a packet the standard forbids.

## Runtime Evidence

A focused CONNECT encoder/client probe was compiled and run. It compared two valid controls against the invalid combination `client_id=""` and `clean_session=0`, then checked both direct encoding and the public `MqttClient_Connect` write path.

Build and run:

```text
build_exit_code=0
run_exit_code=0
```

Key output:

```text
control_nonempty_clean0 fixed_type=0x10 remain_len=15 flags=0x00 clean_bit=0 client_id_len=3
control_empty_clean1 fixed_type=0x10 remain_len=12 flags=0x02 clean_bit=1 client_id_len=0
issue_empty_clean0 fixed_type=0x10 remain_len=12 flags=0x00 clean_bit=0 client_id_len=0
client_connect_issue rc=-18 ack_return_code=2 written_len=14
client_connect_issue_written fixed_type=0x10 remain_len=12 flags=0x00 clean_bit=0 client_id_len=0
```

The controls show valid cases are encoded as expected. The issue case shows both direct encoding and `MqttClient_Connect` write a v3.1.1 CONNECT with zero-length ClientId and CleanSession clear.

## Impact

Applications using wolfMQTT can emit a non-conformant CONNECT packet. A compliant broker will reject it with Identifier Rejected; a permissive broker may still receive a session request the MQTT 3.1.1 client was not allowed to send.

## Fix Direction

In `MqttEncode_Connect` or before it in `MqttClient_Connect`, reject MQTT 3.1.1 CONNECT inputs where `XSTRLEN(client_id) == 0 && clean_session == 0`.
