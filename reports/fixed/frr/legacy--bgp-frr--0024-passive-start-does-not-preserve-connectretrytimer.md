# Passive start does not preserve ConnectRetryTimer

## Summary

[RFC 4271](https://www.rfc-editor.org/rfc/rfc4271.html) requires passive start events in `Idle` to start `ConnectRetryTimer` with the initial value and enter `Active`. FRR reaches passive `Active`, but its `Active` timer setup cancels `t_connect`.

## Standard Requirement

Official standard: [RFC 4271 Section 8.2.1](https://www.rfc-editor.org/rfc/rfc4271.html#section-8.2.1)

[RFC 4271 Section 8.2.2](https://www.rfc-editor.org/rfc/rfc4271.html#section-8.2.2):

```text
In response to a ManualStart_with_PassiveTcpEstablishment event
      (Event 4) or AutomaticStart_with_PassiveTcpEstablishment event
      (Event 5), the local system:

        - initializes all BGP resources,

        - sets the ConnectRetryCounter to zero,

        - starts the ConnectRetryTimer with the initial value,

        - listens for a connection that may be initiated by the remote
          peer, and

        - changes its state to Active.
```

So the passive-start result must be `Active` with `ConnectRetryTimer` started.

## Relevant Source Code

FRR defines RFC event codes 4 and 5:

```c
/* bgpd/bgpd.h:1428-1433 */
enum bgp_fsm_rfc_codes {
	BGP_FSM_ManualStart = 1,
	BGP_FSM_ManualStop = 2,
	BGP_FSM_AutomaticStart = 3,
	BGP_FSM_ManualStart_with_PassiveTcpEstablishment = 4,
	BGP_FSM_AutomaticStart_with_PassiveTcpEstablishment = 5,
```

But the internal FSM handles `Idle` start as `BGP_Start -> Connect`:

```c
/* bgpd/bgp_fsm.c:3198-3201 */
/* Idle state: In Idle state, all events other than BGP_Start is
   ignored.  With BGP_Start event, finite state machine calls
   bgp_start(). */
{bgp_start, Connect}, /* BGP_Start */
```

For passive mode, `bgp_connect()` queues a failed-open event instead of initiating TCP:

```c
/* bgpd/bgp_network.c:951-954 */
/* If the peer is passive mode, force to move to Active mode. */
if (CHECK_FLAG(peer->flags, PEER_FLAG_PASSIVE)) {
	BGP_EVENT_ADD(connection, TCP_connection_open_failed);
	return connect_error;
}
```

`Connect` can arm `t_connect`:

```c
/* bgpd/bgp_fsm.c:390-400 */
case Connect:
	event_cancel(&connection->t_start);
	if (CHECK_FLAG(peer->flags, PEER_FLAG_TIMER_DELAYOPEN))
		BGP_TIMER_ON(connection->t_connect, bgp_connect_timer,
			     (peer->v_delayopen + peer->v_connect));
	else
		BGP_TIMER_ON(connection->t_connect, bgp_connect_timer,
			     peer->v_connect);
```

The queued failure moves `Connect` to `Active`; passive `Active` cancels `t_connect`:

```c
/* bgpd/bgp_fsm.c:3226 */
{bgp_connect_fail, Active}, /* TCP_connection_open_failed */

/* bgpd/bgp_fsm.c:407-417 */
case Active:
	event_cancel(&connection->t_start);
	if (CHECK_FLAG(peer->flags, PEER_FLAG_PASSIVE) ||
	    CHECK_FLAG(peer->sflags, PEER_STATUS_NSF_WAIT) ||
	    CHECK_FLAG(peer->sflags, PEER_STATUS_BFD_STRICT_HOLD)) {
		event_cancel(&connection->t_connect);
	} else {
```

The CLI sets `PEER_FLAG_PASSIVE`:

```c
/* bgpd/bgp_vty.c:6331-6335 */
"Don't send open messages to this neighbor\n")
{
	int idx_peer = 1;
	return peer_flag_set_vty(vty, argv[idx_peer]->arg, PEER_FLAG_PASSIVE);
}
```

## Implementation Behavior

For a passive neighbor, FRR uses `BGP_Start`, maps `Idle` to `Connect`, queues `TCP_connection_open_failed`, maps that to `Active`, then cancels `t_connect` because `PEER_FLAG_PASSIVE` is set.

## Inconsistency Reason

The RFC passive-start action requires `ConnectRetryTimer` to be started with the initial value in the resulting `Active` state. FRR's resulting passive `Active` state explicitly cancels that timer, so the required timer action is not preserved.

## Runtime Evidence

A focused checker inspected the passive-start requirement, CLI flag, BGP_Start dispatch, connect-failure transition, and state-specific timer setup. Its assertions found that Connect arms t_connect, passive connection handling leads to Active, and Active-passive handling cancels t_connect. Four candidate control/reproducer pairs all exited 0. The output follows source scheduling decisions; it does not measure a timer in a running peer session. No live FRR/topotest environment was available in the recorded Windows snapshot.

Recorded output and checks (local artifact paths omitted):

```json
{
  "standard_checks": {
    "passive_start_events_defined": true,
    "requires_start_connect_retry_timer_initial_value": true,
    "requires_state_active": true
  },
  "implementation_checks": {
    "rfc_event_4_5_enums_exist": true,
    "internal_fsm_uses_single_bgp_start": true,
    "passive_cli_sets_peer_flag": true,
    "bgp_connect_forces_passive_to_failed_event": true,
    "connect_state_arms_t_connect": true,
    "connect_fail_transitions_to_active": true,
    "active_passive_cancels_t_connect": true
  }
}
```

## Impact

Passive sessions can remain in `Active` without the RFC-required passive-start `ConnectRetryTimer`. Packet-level interoperability failure was not demonstrated.

## Fix Direction

Preserve a running `ConnectRetryTimer` for passive-start `Active` sessions, or add an explicit RFC-equivalent passive-start path that starts `t_connect` with the initial retry value while listening for the remote peer.
