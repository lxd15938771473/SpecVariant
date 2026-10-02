# Client can send CONNECT twice on one Network Connection

## Summary

The confirmed scope is the wolfMQTT client API. On the same underlying Network Connection, an application can call `MqttClient_Connect` twice and the library writes two MQTT CONNECT packets. The broker path already rejects a second CONNECT and is not the confirmed gap.

## Standard Requirement

Reference: [MQTT v3.1.1 specification, Section 3.1, CONNECT - Client requests a connection to a Server](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html).

```text
After a Network Connection is established by a Client to a Server, the first Packet sent from the Client to the Server MUST be a CONNECT Packet [MQTT-3.1.0-1].

A Client can only send the CONNECT Packet once over a Network Connection. The Server MUST process a second CONNECT Packet sent from a Client as a protocol violation and disconnect the Client [MQTT-3.1.0-2].
```

Thus, a client must not send a second CONNECT on the same Network Connection. If it needs a new handshake, it should close the old connection and establish a new one first.

## Relevant Source Code

`MqttClient_Connect` validates arguments but does not check whether CONNECT has already been sent on the current Network Connection:

```c
int MqttClient_Connect(MqttClient *client, MqttConnect *mc_connect)
{
    int rc;
    ...
    if (client == NULL || mc_connect == NULL) {
        return MQTT_TRACE_ERROR(MQTT_CODE_ERROR_BAD_ARG);
    }
```

When passed a new `MqttConnect` object with `stat.write == MQTT_MSG_BEGIN`, the function encodes and writes CONNECT again:

```c
rc = MqttEncode_Connect(client->tx_buf, client->tx_buf_len, mc_connect);
...
mc_connect->stat.write = MQTT_MSG_HEADER;
...
rc = MqttPacket_Write(client, client->tx_buf, xfer);
```

After success, it resets only the per-object write state:

```c
/* reset state */
mc_connect->stat.write = MQTT_MSG_BEGIN;
```

`MqttSocket_Connect` does not call `net->connect` if the transport is already marked connected; it reuses the existing connection.

## Implementation Behavior

`MQTT_CLIENT_FLAG_IS_CONNECTED` represents the underlying transport. After the first `MqttClient_Connect` succeeds, the client does not retain connection-level state saying CONNECT has already been used on that Network Connection. A second fresh `MqttConnect` object re-enters the encode/write path and sends another CONNECT over the same transport.

## Inconsistency Reason

MQTT 3.1.1 permits only one CONNECT packet per Network Connection. wolfMQTT tracks transport state and per-object write state, but it lacks a connection-level CONNECT-once guard, so it can violate MQTT-3.1.0-2.

## Runtime Evidence

A focused reproducer was compiled and run on a mock network. It initialized the client, established one transport connection, and called `MqttClient_Connect` twice with fresh CONNECT objects.

Observed output:

```text
rc_init=0 rc_net=0 rc1=0 rc2=0 connect_calls=1 write_calls=2 sent_len=42
frame1 type=1 first_byte=0x10 len=21
frame2 type=1 first_byte=0x10 len=21
frames=2
```

Observed result: `connect_calls=1` shows only one underlying Network Connection was established, while `write_calls=2` and the two `type=1` / `first_byte=0x10` frames show that two MQTT CONNECT packets were written on that same connection.

Existing probe controls also observed:

```text
NetConnect followed by MqttClient_Connect wrote CONNECT as first packet
same Network Connection accepted two MqttClient_Connect calls and wrote two CONNECT frames
```

## Impact

If an application repeats `MqttClient_Connect` without first calling `MqttClient_NetDisconnect`, wolfMQTT client can send a second CONNECT that a conforming broker must treat as a protocol violation and disconnect.

## Fix Direction

Add connection-level MQTT handshake state such as `CONNECT_NOT_SENT`, `CONNECT_SENT`, and `CONNACK_ACCEPTED`. Once CONNECT has been written on a Network Connection, another `MqttClient_Connect` call should return an error until `MqttClient_NetDisconnect` clears that connection state.
