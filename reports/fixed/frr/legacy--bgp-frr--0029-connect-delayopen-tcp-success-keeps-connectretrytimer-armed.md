# Connect DelayOpen TCP success keeps ConnectRetryTimer armed

## Summary

When FRR handles `Connect + TCP_connection_open_w_delay`, it starts `DelayOpenTimer` but does not stop `ConnectRetryTimer`. Because the FSM remains in `Connect`, the common timer reset path arms `t_connect` again. This conflicts with [RFC 4271](https://www.rfc-editor.org/rfc/rfc4271.html).

## Standard Requirement

Official standard: [RFC 4271 Section 8.2.2](https://www.rfc-editor.org/rfc/rfc4271.html#section-8.2.2), Connect state, [RFC 4271 Section 8.2.2](https://www.rfc-editor.org/rfc/rfc4271.html#section-8.2.2).

```text
If the TCP connection succeeds (Event 16 or Event 17), the local
system checks the DelayOpen attribute prior to processing.  If the
DelayOpen attribute is set to TRUE, the local system:

  - stops the ConnectRetryTimer (if running) and sets the
    ConnectRetryTimer to zero,

  - sets the DelayOpenTimer to the initial value, and
```

Meaning: after delayed TCP success in `Connect`, `ConnectRetryTimer` must be stopped before waiting on `DelayOpenTimer`.

## Relevant Source Code

`bgpd/bgp_fsm.c:2540-2598` starts `t_delayopen`; no `event_cancel(&connection->t_connect)` appears in this handler.

```c
static enum bgp_fsm_state_progress
bgp_connect_success_w_delayopen(struct peer_connection *connection)
{
	...
	peer->v_delayopen = peer->delayopen;

	if (!event_is_scheduled(connection->t_delayopen))
		BGP_TIMER_ON(connection->t_delayopen, bgp_delayopen_timer, peer->v_delayopen);
	...
	return BGP_FSM_SUCCESS;
}
```

`bgpd/bgp_fsm.c:3222-3224` keeps the FSM in `Connect` for delayed TCP success.

```c
{bgp_connect_success, OpenSent}, /* TCP_connection_open */
{bgp_connect_success_w_delayopen,
 Connect},		    /* TCP_connection_open_w_delay */
```

`bgpd/bgp_fsm.c:3403-3435` calls the handler and then resets timers after success.

```c
if (FSM[connection->status - 1][event - 1].func)
	ret = (*(FSM[connection->status - 1][event - 1].func))(connection);
...
case BGP_FSM_SUCCESS:
	...
	bgp_timer_set(connection);
	break;
```

`bgpd/bgp_fsm.c:390-400` arms `t_connect` while in `Connect`.

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

For `Connect + TCP_connection_open_w_delay`, FRR runs `bgp_connect_success_w_delayopen()`, starts `t_delayopen`, returns `BGP_FSM_SUCCESS`, stays in `Connect`, then `bgp_event_update()` calls `bgp_timer_set()`. The `Connect` branch re-arms `t_connect`.

## Inconsistency Reason

The standard requires `ConnectRetryTimer` to be stopped and set to zero when delayed TCP success occurs. FRR does not cancel `t_connect` in the DelayOpen success handler and then re-arms it through `bgp_timer_set()`. Therefore `ConnectRetryTimer` can remain active while `DelayOpenTimer` is running.

## Runtime Evidence

The existing source reproducer returned ok=true. A focused source-path model began in Connect and applied TCP_connection_open_w_delay with DelayOpen enabled. It found that the handler starts t_delayopen without canceling t_connect, the FSM remains in Connect, and subsequent bgp_timer_set arms t_connect. The model therefore retained or rearmed ConnectRetryTimer while DelayOpenTimer ran. No live-daemon timer observation was recorded.

Recorded output and checks (local artifact paths omitted):

```text
"ok": true,
"inconsistency_reason": "The PEER_FLAG_TIMER_DELAYOPEN path in bgp_connect_success_w_delayopen starts t_delayopen but the Connect/Active timer setup continues to arm t_connect or does not branch as RFC 4271 requires for this timer action."
```

```text
source checks:
- delayopen handler cancels t_connect: False
- delayopen handler starts t_delayopen: True
- FSM keeps next state Connect for TCP_connection_open_w_delay: True
- bgp_event_update calls bgp_timer_set after success: True
- Connect state arms t_connect: True

modeled event:
- initial_state: Connect
- event: TCP_connection_open_w_delay
- delayopen_true: True
- connectretry_stopped_after_event: False
- modeled_result: t_connect remains or is rearmed while t_delayopen runs
```

## Impact

The implementation may fire `ConnectRetryTimer` during the DelayOpen wait, even though [RFC 4271](https://www.rfc-editor.org/rfc/rfc4271.html) says that timer should be stopped for this path.

## Fix Direction

Cancel `connection->t_connect` in `bgp_connect_success_w_delayopen()` or adjust `bgp_timer_set()` so the `Connect` state does not re-arm `t_connect` while waiting on `DelayOpenTimer` after delayed TCP success.
