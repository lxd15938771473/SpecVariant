# Connect TCP failure with DelayOpen does not follow the RFC branch

## Summary

[RFC 4271](https://www.rfc-editor.org/rfc/rfc4271.html) requires Connect-state handling of TcpConnectionFails (Event 18) to branch on whether DelayOpenTimer is running. FRR instead maps TCP failure events in Connect to fixed `bgp_connect_fail`/`bgp_stop` paths without checking `t_delayopen` in the failure handler.

## Standard Requirement

Official standard: [RFC 4271 Section 8](https://www.rfc-editor.org/rfc/rfc4271.html#section-8), BGP FSM: [RFC 4271](https://www.rfc-editor.org/rfc/rfc4271.html).

[RFC 4271 Section 8.2.2](https://www.rfc-editor.org/rfc/rfc4271.html#section-8.2.2):

```text
If the TCP connection fails (Event 18), the local system checks
the DelayOpenTimer.  If the DelayOpenTimer is running, the local
system:
  - restarts the ConnectRetryTimer with the initial value,
  - stops the DelayOpenTimer and resets its value to zero,
  - continues to listen for a connection that may be initiated by
    the remote BGP peer, and
  - changes its state to Active.

If the DelayOpenTimer is not running, the local system:
  - stops the ConnectRetryTimer to zero,
  - drops the TCP connection,
  - releases all BGP resources, and
  - changes its state to Idle.
```

Connect-state handling of Event 18 must inspect whether DelayOpenTimer is running before selecting the Active or Idle branch.

## Relevant Source Code

FRR starts `t_delayopen` in DelayOpen mode:

```c
/* bgpd/bgp_fsm.c:2582-2588 */
/* set the DelayOpenTime to the initial value */
peer->v_delayopen = peer->delayopen;

/* Start the DelayOpenTimer if it is not already running */
if (!event_is_scheduled(connection->t_delayopen))
	BGP_TIMER_ON(connection->t_delayopen, bgp_delayopen_timer, peer->v_delayopen);
```

Failure of a nonblocking TCP connect queues `TCP_connection_open_failed`:

```c
/* bgpd/bgp_fsm.c:2480-2486 */
} else {
	if (bgp_debug_neighbor_events(peer))
		zlog_debug("%s [Event] Connect failed %d(%s) for connection %s",
			   peer->host, status, safe_strerror(status),
			   bgp_peer_get_connection_direction_string(connection));
	BGP_EVENT_ADD(connection, TCP_connection_open_failed);
	return;
}
```

FRR uses fixed transitions for TCP failure events in Connect, rather than branching on DelayOpenTimer:

```c
/* bgpd/bgp_fsm.c:3219-3228 */
/* Connect */
{bgp_ignore, Connect}, /* BGP_Start */
{bgp_stop, Idle},      /* BGP_Stop */
{bgp_connect_success, OpenSent}, /* TCP_connection_open */
{bgp_connect_success_w_delayopen, Connect}, /* TCP_connection_open_w_delay */
{bgp_stop, Idle},          /* TCP_connection_closed */
{bgp_connect_fail, Active}, /* TCP_connection_open_failed */
{bgp_connect_fail, Idle},   /* TCP_fatal_error */
{bgp_reconnect, Connect},   /* ConnectRetry_timer_expired */
```

`bgp_connect_fail()` does not check `t_delayopen`:

```c
/* bgpd/bgp_fsm.c:2601-2622 */
/* TCP connect fail */
static enum bgp_fsm_state_progress
bgp_connect_fail(struct peer_connection *connection)
{
	struct peer *peer = connection->peer;

	if (peer_dynamic_neighbor_no_nsf(peer)) {
		...
		peer_delete(peer);
		return BGP_FSM_FAILURE_AND_DELETE;
	}

	bgp_nht_interface_events(peer);

	return bgp_stop(connection);
}
```

## Implementation Behavior

FRR can start DelayOpenTimer and cancel it while sending OPEN on expiry. For TCP failures in Connect, however, the internal event type selects the branch: TCP_connection_open_failed always goes to Active, while TCP_connection_closed and TCP_fatal_error go to Idle. The branch is not selected by whether DelayOpenTimer is running.

## Inconsistency Reason

The RFC condition is DelayOpenTimer state; FRR's condition is its internal TCP event type. `bgp_connect_fail()` contains no `event_is_scheduled(connection->t_delayopen)` or equivalent check, so the inspected code does not demonstrate both RFC-required Event 18 branches.

## Runtime Evidence

The baseline control and source reproducer returned ok=true. Replay, boundary, and missing variants passed the same source checks. Focused assertions checked that DelayOpenTimer starts on delayed TCP success, that Connect maps open-failed to Active and closed/fatal to Idle, and that bgp_connect_fail contains no DelayOpenTimer test.

Recorded output and checks (local artifact paths omitted):

```json
{
  "ok": true,
  "mode": "positive_control"
}
```

```json
{
  "ok": true,
  "mode": "reproducer",
  "risk_class": "baseline"
}
```

```text
delayopen_timer_started_on_tcp_success_with_delayopen = true
connect_open_failed_goes_active = true
connect_closed_goes_idle = true
connect_fatal_goes_idle = true
bgp_connect_fail_checks_delayopen_timer = false
```

```text
where bgpd  -> not found
where vtysh -> not found
bgpd.exe    -> absent in source tree
vtysh.exe   -> absent in source tree
```

## Impact

This is an FSM failure-path mismatch. With DelayOpen enabled, TCP failure in Connect should select cleanup actions and target state according to DelayOpenTimer state. FRR's fixed event mapping can select an Active or Idle result that differs from the RFC branch.

## Fix Direction

Branch on `t_delayopen` in Connect-state handling of TcpConnectionFails. If running, restart ConnectRetryTimer, stop and clear DelayOpenTimer, and transition to Active. Otherwise, stop ConnectRetryTimer, close the connection, and transition to Idle. Add TCP-failure FSM coverage with DelayOpen enabled.
