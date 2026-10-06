# WebSocket non-binary data frame does not close the connection

## Summary

MQTT 3.1.1 requires MQTT over WebSocket to use only binary data frames. If any other type of data frame is received, the recipient must close the Network Connection. wolfMQTT's WebSocket receive callbacks do not check the frame type; they append the received bytes directly to the MQTT receive buffer. The runtime reproducer covers the example client WebSocket callback, and the broker callback has the same static gap.

## Standard Requirement

Official standard: [MQTT Version 3.1.1](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html)

MQTT 3.1.1 Section 6, Using WebSocket as a network transport:

```text
If any other type of data frame is received the recipient MUST close the Network Connection [MQTT-6.0.0-1].
```

Therefore, when MQTT over WebSocket receives a text frame or any other non-binary data frame, it must not continue processing that payload as an MQTT byte stream. It must close the network connection.

## Relevant Source Code

The client WebSocket receive path in `implementions/wolfMQTT-master/examples/websocket/net_libwebsockets.c:79-97` appends received bytes to `rx_buffer` and closes only on buffer overflow:

```c
else if (reason == LWS_CALLBACK_CLIENT_RECEIVE) {
    if (in && len > 0) {
        if (net->rx_len + len <= sizeof(net->rx_buffer)) {
            XMEMCPY(net->rx_buffer + net->rx_len, in, len);
            net->rx_len += len;
        } else {
            net->status = -1;
            return -1; /* close connection */
        }
    }
}
```

The client write path uses binary frames in `implementions/wolfMQTT-master/examples/websocket/net_libwebsockets.c:220-291`:

```c
ret = lws_write(net->wsi, ws_buf + LWS_PRE, buf_len, LWS_WRITE_BINARY);
```

The broker WebSocket receive path in `implementions/wolfMQTT-master/src/mqtt_broker.c:1117-1138` also appends received bytes without checking the frame type:

```c
else if (reason == LWS_CALLBACK_RECEIVE) {
    BrokerClient **bc_ptr = (BrokerClient**)lws_wsi_user(wsi);
    if (bc_ptr == NULL || *bc_ptr == NULL) return 0;
    bc = *bc_ptr;
    ws = (BrokerWsCtx*)bc->ws_ctx;
    if (ws == NULL || in == NULL || len == 0) return 0;

    if (ws->rx_len + len <= sizeof(ws->rx_buffer)) {
        XMEMCPY(ws->rx_buffer + ws->rx_len, in, len);
        ws->rx_len += len;
    }
}
```

The broker write path uses binary frames in `implementions/wolfMQTT-master/src/mqtt_broker.c:1154-1165`:

```c
int n = lws_write(wsi, ws->tx_pending + LWS_PRE,
    ws->tx_len, LWS_WRITE_BINARY);
```

A source search found receive callbacks and binary write calls, but no use of `lws_frame_is_binary` or an equivalent opcode check.

## Implementation Behavior

wolfMQTT ensures that MQTT over WebSocket writes use `LWS_WRITE_BINARY`, but the receive side does not verify that inbound data frames are binary. A non-binary frame payload can therefore enter the MQTT receive buffer and be passed onward to the MQTT parser as though it were ordinary MQTT wire data.

## Inconsistency Reason

The standard requires the recipient to close the network connection when a non-binary WebSocket data frame is received. The implementation closes only for conditions such as receive-buffer overflow. Because the frame type is not checked, text frames and other non-binary data frames can be accepted instead of closing the connection.

## Runtime Evidence

A focused WebSocket probe was compiled and run against the real `callback_mqtt` implementation using a stub libwebsockets environment. The stub provided `lws_frame_is_binary()` and marked the inbound data as a non-binary frame, then measured whether the callback checked the frame type, closed the connection, or buffered the payload.

```text
build_exit_code=0
text_frame rc=0 status=1 rx_len=2 first=10 second=00 frame_check_calls=0 issue=1
expected_for_non_binary=close_connection
RESULT=reproduced_non_binary_ws_frame_is_buffered
run_exit_code=1
```

The callback returned `0`, kept `status=1`, and buffered two bytes. `frame_check_calls=0` shows that the implementation did not query the frame type at all. The probe uses `run_exit_code=1` as the expected issue-found marker, not as a crash indicator.

## Impact

Invalid WebSocket data can reach the MQTT parser instead of immediately closing the connection. This can delay error handling, leave connection state inconsistent, or make upper layers believe they are still processing a valid MQTT stream.

## Fix Direction

Check the WebSocket frame type in both the client and broker receive callbacks. If the received data frame is not binary, set the connection error or close state, return `-1`, and do not append the payload to the MQTT receive buffer. Keep the existing `LWS_WRITE_BINARY` behavior for outbound frames.
