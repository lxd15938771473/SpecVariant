# Received NOTIFICATION errors are not always logged locally

## Summary

Reproduction condition: `SHOULD`-level, profile/config dependent.

This is a real but weak issue. [RFC 4271](https://www.rfc-editor.org/rfc/rfc4271.html) says errors detected in a received NOTIFICATION, such as unrecognized Error Code/Subcode, `SHOULD` be noticed and logged locally. FRR can log them, but only when neighbor-events debug or `bgp log-neighbor-changes` is enabled. In the traditional/default profile that flag is false, so the received error is stored in peer state but no local `zlog_info` is emitted from the normal NOTIFICATION receive path.

The extracted candidate overstates the modality as `MUST`; the standard text is `SHOULD`.

## Standard Requirement

- Official standard: [RFC 4271](https://www.rfc-editor.org/rfc/rfc4271.html)
- Section: `6.4. NOTIFICATION Message Error Handling`
- Evidence: [RFC 4271 Section 6.4](https://www.rfc-editor.org/rfc/rfc4271.html#section-6.4)

```text
If a peer sends a NOTIFICATION message, and the receiver of the
message detects an error in that message, the receiver cannot use a
NOTIFICATION message to report this error back to the peer.  Any such
error (e.g., an unrecognized Error Code or Error Subcode) SHOULD be
noticed, logged locally, and brought to the attention of the
administration of the peer.  The means to do this, however, lies
outside the scope of this document.
```

Meaning: the receiver should not send another NOTIFICATION back for this case, but should notice the error, log it locally, and surface it to administration. The mechanism is outside the RFC scope.

## Relevant Source Code

All paths below are relative to `implementions/frr-master/frr-master`.

`bgpd/bgp_packet.c:2698`

```c
/* Preserve notify code and sub code. */
peer->notify.code = inner.code;
peer->notify.subcode = inner.subcode;
```

`bgpd/bgp_packet.c:2736`

```c
bgp_notify_print(peer, &inner, "received", hard_reset);
```

`bgp_notify_receive()` stores the received NOTIFICATION code/subcode and calls the diagnostic printer.

`bgpd/bgp_debug.c:576`

```c
if (BGP_DEBUG(neighbor_events, NEIGHBOR_EVENTS)
    || CHECK_FLAG(peer->bgp->flags, BGP_FLAG_LOG_NEIGHBOR_CHANGES)) {
```

`bgpd/bgp_debug.c:603`

```c
zlog_info(
	"%%NOTIFICATION%s: %s neighbor %s %d/%d (%s%s) %d bytes %s",
```

The local log is conditional on debug or `BGP_FLAG_LOG_NEIGHBOR_CHANGES`.

`bgpd/bgp_debug.c:518`

```c
return lookup_msg(bgp_notify_msg, code, "Unrecognized Error Code");
```

`bgpd/bgp_debug.c:528`

```c
return lookup_msg(bgp_notify_head_msg, subcode,
		  "Unrecognized Error Subcode");
```

FRR can name unknown Error Code/Subcode, but that naming is reached through the gated print path.

`bgpd/bgp_vty.c:96`

```c
FRR_CFG_DEFAULT_BOOL(BGP_LOG_NEIGHBOR_CHANGES,
	{ .val_bool = true, .match_profile = "datacenter", },
	{ .val_bool = false },
);
```

`bgpd/bgp_vty.c:750`

```c
if (DFLT_BGP_LOG_NEIGHBOR_CHANGES)
	SET_FLAG((*bgp)->flags, BGP_FLAG_LOG_NEIGHBOR_CHANGES);
```

The datacenter profile enables logging by default; the fallback/default profile does not.

`bgpd/bgp_fsm.c:2197`

```c
if (CHECK_FLAG(bgp->flags, BGP_FLAG_LOG_NEIGHBOR_CHANGES)) {
	zlog_info(
```

Neighbor down/up logs are also gated by the same flag.

## Implementation Behavior

When receiving a NOTIFICATION with an unrecognized code/subcode, FRR preserves the values in `peer->notify`, increments notify counters, and records `PEER_DOWN_NOTIFY_RECEIVED`. However, the human-readable local log of the received NOTIFICATION is emitted only if either neighbor-events debug or `BGP_FLAG_LOG_NEIGHBOR_CHANGES` is active.

## Inconsistency Reason

The RFC recommends local logging for detected errors in a received NOTIFICATION. FRR implements logging only under debug/config/profile conditions. Therefore, under the traditional/default profile with logging disabled, the implementation keeps internal diagnostic state but does not satisfy the local logging recommendation.

This should not be reported as a hard protocol violation because the RFC says `SHOULD`, and the notification-to-administration mechanism is explicitly outside the RFC scope.

## Runtime Evidence

The recorded 2026-09-13 run checked four candidates in control and source-reproducer modes; all passed. A focused model inspected notification code/subcode retention, printing, logging gates, and profile defaults. It found that the traditional profile disables neighbor-change logging by default, so the modeled receive path did not call local zlog_info; enabling logging did, and the datacenter default enables it. Unknown-code/subcode strings existed. No bgpd binary was available, so these were source/configuration-model results rather than observed daemon log output.

Recorded output and checks (local artifact paths omitted):

```text
baseline
replay
unknown
error-mapping
```

```json
{
  "candidate_controls": "all passed",
  "candidate_reproducers": "all passed",
  "custom_probe": {
    "receive_preserves_code_subcode": true,
    "receive_calls_notify_print": true,
    "notify_print_logging_is_gated": true,
    "unknown_code_subcode_strings_exist": true,
    "traditional_default_log_neighbor_changes_false": true,
    "datacenter_default_log_neighbor_changes_true": true,
    "traditional_default_local_zlog_info": false,
    "logging_enabled_local_zlog_info": true
  }
}
```

## Impact

With default non-datacenter logging settings, an invalid/unknown NOTIFICATION can close or affect a session while leaving no immediate local log entry that names the received Error Code/Subcode. Operators may need explicit logging/debug or state inspection to notice the condition.

## Fix Direction

For stricter RFC alignment, emit at least one unconditional local warning for errors detected in received NOTIFICATION messages, especially unrecognized Error Code/Subcode. Alternatively, document that this `SHOULD` recommendation is intentionally satisfied only when `bgp log-neighbor-changes`, datacenter profile, or debug logging is enabled.
