# Active retry timer uses backoff value

## Summary

[RFC 4271](https://www.rfc-editor.org/rfc/rfc4271.html) requires ConnectRetryTimer to restart at its initial value after ConnectRetryTimer_Expires in Active. FRR's default path first doubles `peer->v_connect`, then uses the doubled value to rearm `t_connect`.

## Standard Requirement

Official standard: [RFC 4271](https://www.rfc-editor.org/rfc/rfc4271.html)

Section 8.2.2, Active state:

```text
In response to a ConnectRetryTimer_Expires event (Event 9), the local system:
- restarts the ConnectRetryTimer (with initial value),
- initiates a TCP connection ...
- changes its state to Connect.
```

After Event 9 in Active, ConnectRetryTimer should restart at its initial ConnectRetryTime rather than the current backed-off interval.

## Relevant Source Code

Paths below are relative to `implementions/frr-master/frr-master`.

`bgpd/bgpd.c:1783-1785` sets the initial retry value when creating a peer:

```c
/* Set default value. */
peer->v_start = BGP_INIT_START_TIMER;
peer->v_connect = bgp->default_connect_retry;
```

`bgpd/bgp_fsm.c:535-557` handles `ConnectRetryTimer` expiry. In the default case where `peer->connect` is not explicitly configured, FRR doubles `peer->v_connect` before emitting `ConnectRetry_timer_expired`:

```c
static void bgp_connect_timer(struct event *event)
{
	struct peer *peer = connection->peer;
	...
	if (bgp_peer_get_connection_direction(connection) == CONNECTION_INCOMING)
		bgp_stop(connection);
	else {
		if (!peer->connect)
			peer->v_connect = MIN(BGP_MAX_CONNECT_RETRY, peer->v_connect * 2);
		EVENT_VAL(event) = ConnectRetry_timer_expired;
		bgp_event(event);
	}
}
```

`bgpd/bgp_fsm.c:3240-3249` maps `Active + ConnectRetry_timer_expired` to `bgp_start` and `Connect`:

```c
/* Active, */
...
{bgp_start, Connect}, /* ConnectRetry_timer_expired */
```

`bgpd/bgp_fsm.c:3434-3435` applies timer setup after the transition:

```c
/* Make sure timer is set. */
bgp_timer_set(connection);
```

`bgpd/bgp_fsm.c:390-400` then arms `t_connect` with the current `peer->v_connect`:

```c
case Connect:
	event_cancel(&connection->t_start);
	if (CHECK_FLAG(peer->flags, PEER_FLAG_TIMER_DELAYOPEN))
		BGP_TIMER_ON(connection->t_connect, bgp_connect_timer,
			     (peer->v_delayopen + peer->v_connect));
	else
		BGP_TIMER_ON(connection->t_connect, bgp_connect_timer,
			     peer->v_connect);
```

## Implementation Behavior

The default path is:

1. Initialize a new peer's `peer->v_connect` from `bgp->default_connect_retry`.
2. On `t_connect` expiry, `bgp_connect_timer` doubles `peer->v_connect` when `!peer->connect`.
3. The same callback raises ConnectRetry_timer_expired.
4. The Active-state event table transitions to Connect.
5. After the state change, `bgp_timer_set` rearms `t_connect` with the current `peer->v_connect`.

The default path therefore restarts with the backed-off value. Explicitly configuring `peer->connect` skips the doubling branch, so the finding primarily concerns the default unconfigured connect timer.

## Inconsistency Reason

The RFC requires Event 9 in Active to restart ConnectRetryTimer with its initial value. FRR doubles that value before using it for the next `t_connect`. Transitioning to Connect and initiating TCP are implemented; the reported mismatch is the timer value.

## Runtime Evidence

The recorded rerun at 2026-09-13 12:46:21 +08:00 checked four candidate control/reproducer pairs. Every control returned ok=true. A focused source-path model began with v_connect=30, applied the timer-expiry callback, observed v_connect become 60 before event dispatch, and used the current value to restart the Connect-state timer. No runnable bgpd was present, so 30-to-60 is a modeled interval change, not a measured retry delay.

Recorded output and checks (local artifact paths omitted):

```json
{
  "initial_v_connect": 30,
  "bgp_connect_timer_updates_v_connect_before_event": 60,
  "new_connect_state_timer_uses": "peer->v_connect",
  "timer_restarted_with_initial_value": false
}
```

## Impact

Default Active retry behavior may wait with exponential backoff instead of the RFC-specified initial ConnectRetryTimer value. This is a strict FSM conformance issue; operationally it can delay later connection attempts after repeated failures.

## Fix Direction

For `Active + ConnectRetry_timer_expired`, reset the timer value used for the next `t_connect` to the configured/default initial `ConnectRetryTime`, or document the exponential backoff as an intentional RFC deviation.
