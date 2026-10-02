# FSM exception paths can stop without FSM Error notification

## Summary

This is a real issue, but the scope should be narrow. FRR does send `Finite State Machine Error` for several unexpected received packet paths. The gap is in `bgp_fsm_exception()`: some FSM-table unexpected-event entries in OpenSent and OpenConfirm call this function, and it stops the peer without sending a NOTIFICATION.

## Standard Requirement

Standard: [RFC 4271](https://www.rfc-editor.org/rfc/rfc4271.html), sections 6.6 and 8, [RFC 4271 Section 6.6](https://www.rfc-editor.org/rfc/rfc4271.html#section-6.6)

```text
Any error detected by the BGP Finite State Machine (e.g., receipt of
an unexpected event) is indicated by sending the NOTIFICATION message
with the Error Code Finite State Machine Error.
```

The state-machine rules repeat this for specific states. For example, in OpenSent, "any other event" including Events 9, 11-13, 20, and 25-28 must send a NOTIFICATION with `Finite State Machine Error` ([RFC 4271 Section 8.2.2](https://www.rfc-editor.org/rfc/rfc4271.html#section-8.2.2)). OpenConfirm has the same FSM Error notification requirement for its "any other event" class, including timer/internal unexpected-event paths.

## Relevant Source Code

`bgpd/bgp_fsm.c:2403-2425` is the normal helper that sends NOTIFICATION before stopping:

```c
/* something went wrong, send notify and tear down */
enum bgp_fsm_state_progress
bgp_stop_with_notify(struct peer_connection *connection, uint8_t code,
		     uint8_t sub_code)
{
	struct peer *peer = connection->peer;

	/* Send notify to remote peer */
	bgp_notify_send(connection, code, sub_code);
...
	return bgp_stop(connection);
```

`bgpd/bgp_fsm.c:2808-2820` uses that helper for unexpected packet events:

```c
/* FSM error, unexpected event.  This is error of BGP connection. So cut the
   peer and change to Idle status. */
static enum bgp_fsm_state_progress
bgp_fsm_event_error(struct peer_connection *connection)
{
	flog_err(EC_BGP_FSM, "%s [FSM] unexpected packet received in state %s",
		 peer->host,
		 lookup_msg(bgp_status_msg, connection->status, NULL));

	return bgp_stop_with_notify(connection, BGP_NOTIFY_FSM_ERR,
				    bgp_fsm_error_subcode(connection->status));
```

`bgpd/bgp_fsm.c:3136-3149` is the problematic exception helper:

```c
/* This is to handle unexpected events.. */
static enum bgp_fsm_state_progress
bgp_fsm_exception(struct peer_connection *connection)
{
	flog_err(EC_BGP_FSM,
		 "%s(%s) [FSM] Unexpected event %s in state %s, prior events %s, %s, fd %d",
		 peer->host, bgp_peer_get_connection_direction_string(connection),
		 bgp_event_str[peer->cur_event],
		 lookup_msg(bgp_status_msg, connection->status, NULL),
		 bgp_event_str[peer->last_event], bgp_event_str[peer->last_major_event],
		 connection->fd);
	return bgp_stop(connection);
```

`bgpd/bgp_fsm.c:3269-3277` maps several OpenSent unexpected events to `bgp_fsm_exception()`:

```c
		{bgp_fsm_exception, Idle}, /* ConnectRetry_timer_expired   */
		{bgp_fsm_holdtime_expire, Idle}, /* Hold_Timer_expired */
		{bgp_fsm_exception, Idle},   /* KeepAlive_timer_expired      */
		{bgp_fsm_exception, Idle},   /* DelayOpen_timer_expired */
		{bgp_fsm_open, OpenConfirm}, /* Receive_OPEN_message         */
		{bgp_fsm_event_error, Idle}, /* Receive_KEEPALIVE_message    */
		{bgp_fsm_event_error, Idle}, /* Receive_UPDATE_message       */
		{bgp_fsm_event_error, Idle}, /* Receive_NOTIFICATION_message */
		{bgp_fsm_exception, Idle},   /* Clearing_Completed           */
```

Some received-packet paths are handled correctly. For example, `bgpd/bgp_packet.c:2412-2418` sends `BGP_NOTIFY_FSM_ERR` when UPDATE is received outside Established:

```c
	if (!peer_established(connection)) {
		flog_err(EC_BGP_INVALID_STATUS, "%s [FSM] Update packet received under status %s",
			 peer->host, lookup_msg(bgp_status_msg, connection->status, NULL));
		bgp_notify_send(connection, BGP_NOTIFY_FSM_ERR,
				bgp_fsm_error_subcode(connection->status));
		return BGP_Stop;
```

## Implementation Behavior

FRR has two different unexpected-event handlers. `bgp_fsm_event_error()` sends `BGP_NOTIFY_FSM_ERR`; `bgp_fsm_exception()` only logs and calls `bgp_stop()`. Since `bgp_event_update()` dispatches through the FSM table (`bgpd/bgp_fsm.c:3373-3406`), any OpenSent or OpenConfirm table entry mapped to `bgp_fsm_exception()` can close the connection without the FSM Error NOTIFICATION.

## Inconsistency Reason

[RFC 4271](https://www.rfc-editor.org/rfc/rfc4271.html) requires FSM-detected unexpected events to be indicated with a NOTIFICATION using Error Code `Finite State Machine Error`. FRR satisfies this for several packet handlers, but the `bgp_fsm_exception()` branch is inconsistent because it detects/logs an unexpected event and then stops the peer without notification.

## Runtime Evidence

Four candidate control/reproducer pairs passed. Additional assertions found that the exception path logs an unexpected event, calls bgp_stop, contains no notification call, and is selected by unexpected timer entries in OpenSent. This is a source-path observation; no unexpected event was injected into a running daemon. A runnable bgpd/zebra/vtysh, configured build, and WSL FRR/topotest environment were unavailable.

Recorded output and checks (local artifact paths omitted):

```text
exception logs unexpected event: true
exception calls bgp_stop: true
exception has no notify path: true
OpenSent unexpected timer entries use exception: true
```

| Candidate | control | reproducer |
|---|---:|---:|
| `baseline` | passed | passed |
| `replay` | passed | passed |
| `state-order` | passed | passed |
| `error-mapping` | passed | passed |

## Impact

Some FSM unexpected-event paths can close the connection without the RFC-required FSM Error NOTIFICATION. This mainly affects strict conformance and peer diagnostics for those exceptional state/event combinations.

## Fix Direction

Change `bgp_fsm_exception()` to use `bgp_stop_with_notify(connection, BGP_NOTIFY_FSM_ERR, bgp_fsm_error_subcode(connection->status))`, or split internal-only cleanup events from true RFC unexpected events and notify only for the latter.
