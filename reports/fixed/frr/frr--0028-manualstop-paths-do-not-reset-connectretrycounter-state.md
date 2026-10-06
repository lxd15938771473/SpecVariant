# ManualStop paths do not reset ConnectRetryCounter state

## Summary

[RFC 4271](https://www.rfc-editor.org/rfc/rfc4271.html) requires the FSM to clear ConnectRetryCounter on ManualStop (Event 2) in several non-Idle states. FRR bgpd does not implement that counter, and its shared `BGP_Stop`/`bgp_stop()` path does not reset equivalent counting state for manual stops in Connect, Active, OpenSent, OpenConfirm, or Established.

## Standard Requirement

Official standard: [RFC 4271 Section 8](https://www.rfc-editor.org/rfc/rfc4271.html#section-8), BGP FSM: [RFC 4271](https://www.rfc-editor.org/rfc/rfc4271.html).

[RFC 4271 Section 8](https://www.rfc-editor.org/rfc/rfc4271.html#section-8) lists ConnectRetryCounter as a mandatory session attribute:

```text
Session attributes required (mandatory) for each connection are:
   1) State
   2) ConnectRetryCounter
   3) ConnectRetryTimer
   4) ConnectRetryTime
...
The ConnectRetryCounter indicates the number of times a BGP
peer has tried to establish a peer session.
```

[RFC 4271 Section 8.2.2](https://www.rfc-editor.org/rfc/rfc4271.html#section-8.2.2) specifies handling of ManualStop (Event 2) in Connect:

```text
In response to a ManualStop event (Event 2), the local system:
  - drops the TCP connection,
  - releases all BGP resources,
  - sets ConnectRetryCounter to zero,
  - stops the ConnectRetryTimer and sets ConnectRetryTimer to
    zero, and
  - changes its state to Idle.
```

The ManualStop branches for Active, OpenSent, OpenConfirm, and Established also require ConnectRetryCounter=0. These branches need to maintain and clear the same retry counter before transitioning to Idle.

## Relevant Source Code

FRR's peer structure contains configured and effective timer values, but no ConnectRetryCounter field:

```c
/* bgpd/bgpd.h:2039-2048 */
/* Configured timer values. */
_Atomic uint32_t holdtime;
_Atomic uint32_t keepalive;
_Atomic uint32_t connect;
_Atomic uint32_t routeadv;
_Atomic uint32_t delayopen;

/* Timer values. */
_Atomic uint32_t v_start;
_Atomic uint32_t v_connect;
```

In Connect, BGP_Stop dispatches to `bgp_stop()` and transitions to Idle:

```c
/* bgpd/bgp_fsm.c:3219-3228 */
/* Connect */
{bgp_ignore, Connect}, /* BGP_Start */
{bgp_stop, Idle},      /* BGP_Stop */
{bgp_connect_success, OpenSent}, /* TCP_connection_open */
...
{bgp_reconnect, Connect},   /* ConnectRetry_timer_expired */
```

`bgp_stop()` cancels ConnectRetryTimer, but does not clear ConnectRetryCounter or reset `peer->v_connect`:

```c
/* bgpd/bgp_fsm.c:2279-2284 */
/* Stop all timers. */
event_cancel(&connection->t_start);
event_cancel(&connection->t_connect);
event_cancel(&connection->t_holdtime);
event_cancel(&connection->t_routeadv);
event_cancel(&connection->t_delayopen);
```

```c
/* bgpd/bgp_fsm.c:2351-2364 */
/* Reset keepalive and holdtime */
if (CHECK_FLAG(peer->flags, PEER_FLAG_TIMER)) {
	peer->v_keepalive = peer->keepalive;
	peer->v_holdtime = peer->holdtime;
} else {
	peer->v_keepalive = bgp->default_keepalive;
	peer->v_holdtime = bgp->default_holdtime;
}

/* Reset DelayOpenTime */
if (CHECK_FLAG(peer->flags, PEER_FLAG_TIMER_DELAYOPEN))
	peer->v_delayopen = peer->delayopen;
else
	peer->v_delayopen = bgp->default_delayopen;
```

`peer->v_connect` is the effective connection-retry interval and grows when the connect timer expires:

```c
/* bgpd/bgp_fsm.c:395-400 */
if (CHECK_FLAG(peer->flags, PEER_FLAG_TIMER_DELAYOPEN))
	BGP_TIMER_ON(connection->t_connect, bgp_connect_timer,
		     (peer->v_delayopen + peer->v_connect));
else
	BGP_TIMER_ON(connection->t_connect, bgp_connect_timer,
		     peer->v_connect);
```

```c
/* bgpd/bgp_fsm.c:551-557 */
if (bgp_peer_get_connection_direction(connection) == CONNECTION_INCOMING)
	bgp_stop(connection);
else {
	if (!peer->connect)
		peer->v_connect = MIN(BGP_MAX_CONNECT_RETRY, peer->v_connect * 2);
	EVENT_VAL(event) = ConnectRetry_timer_expired;
	bgp_event(event); /* bgp_event unlocks peer */
```

`bgp_start()` initiates the connection but does not reset `peer->v_connect`:

```c
/* bgpd/bgp_fsm.c:2645-2651 */
static enum bgp_fsm_state_progress bgp_start(struct peer_connection *connection)
{
	struct peer *peer = connection->peer;
	enum connect_result status;

	bgp_peer_conf_if_to_su_update(connection);
```

```c
/* bgpd/bgp_fsm.c:2727-2774 */
status = bgp_connect(connection);
...
return BGP_FSM_SUCCESS;
```

## Implementation Behavior

FRR can cancel the connection-retry timer and transition to Idle through the BGP_Stop path corresponding to ManualStop, but it lacks the RFC-required ConnectRetryCounter reset. Its separate retry-related value `peer->v_connect` doubles after retry-timer expiry and is not restored by manual stop followed by Start.

## Inconsistency Reason

The standard requires ConnectRetryCounter=0 before ManualStop transitions from Connect, Active, OpenSent, OpenConfirm, or Established to Idle. FRR instead follows BGP_Stop -> bgp_stop -> cancel t_connect, with no counter field or equivalent reset. DampPeerOscillations is optional, but ConnectRetryCounter itself is listed as mandatory, so absence of damping does not explain this gap.

## Runtime Evidence

The baseline positive control returned ok=true, and the source reproducer returned ok=true. Focused assertions checked the peer structure and start, stop, and timer-expiry paths. They found no counter field, confirmed ManualStop-related BGP_Stop dispatch to bgp_stop and retry-interval doubling on expiry, and found no counter or v_connect reset in stop/start.

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
  "mode": "reproducer"
}
```

```text
counter_field_present = false
connect_manual_stop_dispatches_to_bgp_stop = true
connect_timer_expiry_doubles_v_connect = true
bgp_stop_resets_v_connect_or_counter = false
bgp_start_resets_v_connect = false
```

```text
where bgpd  -> not found
where vtysh -> not found
bgpd.exe    -> absent in source tree
vtysh.exe   -> absent in source tree
```

## Impact

This is an FSM state-variable gap. Direct interoperability impact is limited because ConnectRetryCounter is not a BGP wire field. The manual-stop path nevertheless lacks demonstrable reset behavior for the specified FSM counter, peer-oscillation logic, or retry-state recovery.

## Fix Direction

Implement an explicit ConnectRetryCounter and update it on the RFC-defined reset and increment paths. If FRR intentionally uses `peer->v_connect` as equivalent retry state, restore it to `peer->connect` or `bgp->default_connect_retry` on every ManualStop-related BGP_Stop path. Add manual-stop FSM coverage for Connect, Active, OpenSent, OpenConfirm, and Established.
