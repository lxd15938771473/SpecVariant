# Client inbound errors do not close the network connection

## Summary

wolfMQTT returns an error after inbound packet processing fails, but it does not call the transport `disconnect` callback on that receive path. Runtime evidence confirms that both a malformed inbound `PUBLISH` and an application callback error leave `disconnect_count_after_wait=0`.

## Standard Requirement

Official standard: [MQTT Version 3.1.1](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html)

MQTT 3.1.1 Section 4.8, Handling errors:

```text
[MQTT-4.8.0-2] If the Client or Server encounters a Transient Error while processing an inbound Control Packet it MUST close the Network Connection on which it received that Control Packet.
```

For the client side, this means that if processing an inbound Control Packet fails because of a transient processing error, the client must close the network connection on which that packet was received.

## Relevant Source Code

`implementions/wolfMQTT-master/wolfmqtt/mqtt_client.h:132-138` documents that a non-success message callback return causes a network disconnect:

```c
 *  \param      msg_done    If non-zero value then we have received the entire
                            message and payload.
 *  \return     MQTT_CODE_SUCCESS to remain connected (other values will cause
                net disconnect - see enum MqttPacketResponseCodes)
 */
typedef int (*MqttMsgCb)(struct _MqttClient *client, MqttMessage *message,
    byte msg_new, byte msg_done);
```

`implementions/wolfMQTT-master/src/mqtt_client.c:2032-2073` handles receive-path cleanup. It can clear the internal connection flag for selected fatal protocol errors, but it does not close the transport:

```c
read_cleanup:
    if (recvFatal && MqttClient_IsFatalProtoError(rc)) {
        CLIENT_FORCE_ZERO(client->rx_buf, client->rx_buf_len);
    }
    MqttReadStop(client, mms_stat);

    if (rc < 0) {
        /* ... Only clear the flag; the application tears the
         * transport down via MqttClient_NetDisconnect. ... */
        if (recvFatal && MqttClient_IsFatalProtoError(rc) &&
            (client->flags & MQTT_CLIENT_FLAG_IS_CONNECTED) != 0) {
            (void)MqttClient_Flags(client, MQTT_CLIENT_FLAG_IS_CONNECTED, 0);
        }
        return rc;
    }
```

`implementions/wolfMQTT-master/src/mqtt_client.c:4271-4322` shows that the explicit disconnect API delegates to the socket disconnect path:

```c
int MqttClient_NetDisconnect(MqttClient *client)
{
    if (client == NULL) {
        return MQTT_CODE_ERROR_BAD_ARG;
    }

    return MqttSocket_Disconnect(client);
}
```

`implementions/wolfMQTT-master/src/mqtt_socket.c:554-584` is the path that actually calls the configured `net->disconnect` callback and clears the connected flag:

```c
int MqttSocket_Disconnect(MqttClient *client)
{
    int rc = MQTT_CODE_SUCCESS;
    if (client) {
        if (client->net && client->net->disconnect) {
            rc = client->net->disconnect(client->net->context);
        }
        MqttClient_Flags(client, MQTT_CLIENT_FLAG_IS_CONNECTED, 0);
    }
    return rc;
}
```

The receive error cleanup path returns the error without invoking this disconnect path.

## Implementation Behavior

After inbound `PUBLISH` processing fails, the error is returned to the `MqttClient_WaitMessage` caller. For malformed or protocol-invalid errors, wolfMQTT clears `MQTT_CLIENT_FLAG_IS_CONNECTED`; for a message callback returning `MQTT_CODE_ERROR_CALLBACK`, the connection flag remains set. In both cases, the library does not automatically call `MqttSocket_Disconnect`, so the underlying transport is not immediately closed by the receive path.

## Inconsistency Reason

The standard requires the client to close the Network Connection when it encounters a transient error while processing an inbound Control Packet. wolfMQTT's receive error path only returns an error and leaves actual transport closure to an explicit application call to `MqttClient_NetDisconnect`. That does not satisfy the "MUST close the Network Connection" behavior at the library receive path.

## Runtime Evidence

A focused client probe was compiled and run. It installed a `MqttNet.disconnect` counter, delivered a malformed inbound `PUBLISH`, and then delivered a valid inbound `PUBLISH` whose message callback returned `MQTT_CODE_ERROR_CALLBACK`. For each case, the probe checked whether the wait function closed the transport before returning.

```text
compile_exit_code=0
malformed_wait_rc=-3
malformed_disconnect_count_after_wait=0
malformed_connected_flag_after_wait=0

callback_error_wait_rc=-13
callback_error_disconnect_count_after_wait=0
callback_error_connected_flag_after_wait=1
callback_error_msg_cb_count=1

RESULT: ISSUE inbound error paths did not close transport
run_exit_code=2
```

The malformed case returns an error and clears the internal connected flag, but `disconnect_count_after_wait=0` shows that the transport close callback was not called. The callback-error case also returns an error, leaves the connected flag set, and still has `disconnect_count_after_wait=0`. The probe uses `run_exit_code=2` as the expected issue-found marker for this test.

## Impact

If an application handles only the `MqttClient_WaitMessage` error return, the underlying TCP or TLS connection can remain open after inbound packet processing fails. Resource release and protocol-error isolation then depend on extra application-layer cleanup rather than the required MQTT receive-path behavior.

## Fix Direction

Close the transport in the inbound Control Packet error cleanup path. After determining that the error originated while processing a received Control Packet, call the common `MqttClient_NetDisconnect` or `MqttSocket_Disconnect` logic. Add regression coverage for both malformed/protocol-invalid input and message callback errors.
