# Duplicate prefix in withdrawn routes is applied after NLRI

## Summary

[RFC 4271](https://www.rfc-editor.org/rfc/rfc4271.html) gives a normative `SHOULD` behavior for an UPDATE containing the same prefix in `WITHDRAWN ROUTES` and NLRI: process it as if the withdrawn field did not contain that prefix. FRR instead processes reachable NLRI first and withdrawn NLRI second, so the final state can become withdrawn.

## Standard Requirement

Source: [RFC 4271](https://www.rfc-editor.org/rfc/rfc4271.html), Section 4.3, `UPDATE Message Format`; standard reference: [RFC 4271 Section 4.3](https://www.rfc-editor.org/rfc/rfc4271.html#section-4.3).

```text
An UPDATE message SHOULD NOT include the same address prefix in the
WITHDRAWN ROUTES and Network Layer Reachability Information fields.
However, a BGP speaker MUST be able to process UPDATE messages in
this form.  A BGP speaker SHOULD treat an UPDATE message of this form
as though the WITHDRAWN ROUTES do not contain the address prefix.
```

Interpretation: when the same prefix is present in both fields of one UPDATE, the reachable NLRI should remain effective; the duplicate withdrawn entry should be ignored.

## Relevant Source Code

`frr-master/bgpd/bgp_packet.c:2403`

```c
enum NLRI_TYPES {
	NLRI_UPDATE,
	NLRI_WITHDRAW,
	NLRI_MP_UPDATE,
	NLRI_MP_WITHDRAW,
	NLRI_TYPE_MAX
};
```

`NLRI_UPDATE` sorts before `NLRI_WITHDRAW`.

`frr-master/bgpd/bgp_packet.c:2561`

```c
/* Parse any given NLRIs */
for (int i = NLRI_UPDATE; i < NLRI_TYPE_MAX; i++) {
	if (!nlris[i].nlri)
		continue;

	/* NLRI is processed iff the peer if configured for the specific
	 * afi/safi */
	if (!peer->afc[nlris[i].afi][nlris[i].safi]) {
		zlog_info(
			"%s [Info] UPDATE for non-enabled AFI/SAFI %u/%u",
			peer->host, nlris[i].afi, nlris[i].safi);
		continue;
	}

	/* EoR handled later */
	if (nlris[i].length == 0)
		continue;

	switch (i) {
	case NLRI_UPDATE:
	case NLRI_MP_UPDATE:
		nlri_ret = bgp_nlri_parse(peer, NLRI_ATTR_ARG,
					  &nlris[i], 0);
		break;
	case NLRI_WITHDRAW:
	case NLRI_MP_WITHDRAW:
		nlri_ret = bgp_nlri_parse(peer, NLRI_ATTR_ARG,
					  &nlris[i], 1);
```

The loop dispatches reachable NLRI before withdrawn NLRI.

`frr-master/bgpd/bgp_packet.c:314`

```c
int bgp_nlri_parse(struct peer *peer, struct attr *attr,
		   struct bgp_nlri *packet, bool mp_withdraw)
{
	switch (packet->safi) {
	case SAFI_UNICAST:
	case SAFI_MULTICAST:
		return bgp_nlri_parse_ip(peer, mp_withdraw ? NULL : attr,
					 packet);
```

For withdrawn NLRI, `mp_withdraw` nulls `attr`.

`frr-master/bgpd/bgp_route.c:8763`

```c
/* Normal process. */
if (attr)
	bgp_update(peer, &p, addpath_id, attr, afi, safi,
		   ZEBRA_ROUTE_BGP, BGP_ROUTE_NORMAL, NULL,
		   NULL, 0, 0, NULL, NULL);
else
	bgp_withdraw(peer, &p, addpath_id, afi, safi,
		     ZEBRA_ROUTE_BGP, BGP_ROUTE_NORMAL, NULL,
		     NULL, 0);
```

The same parser therefore installs the reachable NLRI and withdraws the withdrawn NLRI.

`frr-master/bgpd/bgp_route.c:7157`

```c
if (bgp_adj_in_needed(peer, afi, safi) && peer != bgp->peer_self)
	if (!bgp_adj_in_unset(&dest, peer, addpath_id)) {
		peer->stat_pfx_dup_withdraw++;
		return;
	}

/* Lookup withdrawn route. */
assert(dest);
for (pi = bgp_dest_get_bgp_path_info(dest); pi; pi = pi->next)
	if (pi->peer == peer && pi->type == type
	    && pi->sub_type == sub_type
	    && pi->addpath_rx_id == addpath_id)
		break;

/* Withdraw specified route from routing table. */
if (pi && !CHECK_FLAG(pi->flags, BGP_PATH_HISTORY)) {
	bgp_rib_withdraw(p, dest, pi, peer, afi, safi, prd);
```

No same-UPDATE duplicate-prefix exception is visible. If Adj-RIB-In removal succeeds, FRR continues to RIB withdrawal; it returns only when no Adj-RIB-In entry exists.

## Implementation Behavior

For an IPv4 unicast UPDATE that carries prefix `P` in both ordinary NLRI and `WITHDRAWN ROUTES`:

1. `bgp_update_receive` stores both spans in `nlris[]`.
2. The loop processes `NLRI_UPDATE` first, calling `bgp_update` for `P`.
3. The loop then processes `NLRI_WITHDRAW`, nulls `attr`, and calls `bgp_withdraw` for `P`.
4. Final state is withdrawn, not equivalent to omitting `P` from `WITHDRAWN ROUTES`.

## Inconsistency Reason

The standard says the withdrawn copy of `P` should be ignored in this malformed-but-supported UPDATE form. FRR applies both operations in array order. Because withdrawal runs after update, the final route state can be the opposite of the [RFC 4271](https://www.rfc-editor.org/rfc/rfc4271.html) expected state.

## Runtime Evidence

A focused verifier inspected the NLRI enumeration, parsing loop, withdrawal attribute handling, and update/withdraw dispatch. It then modeled one UPDATE containing the same prefix in withdrawn routes and reachable NLRI. The inspected order applied bgp_update before bgp_withdraw, and the model ended with the route absent; the RFC-recommended outcome leaves it present. The focused verifier and existing source reproducer both exited 0. Live testing was unavailable: bgpd, pytest, ExaBGP, passwordless sudo, and a Docker daemon were not available in that recorded environment. The route outcome below is modeled, not a captured daemon RIB change.

Recorded output and checks (local artifact paths omitted):

```text
exit: 0
```

```text
nlri_order={'NLRI_UPDATE': 0, 'NLRI_WITHDRAW': 1, 'NLRI_MP_UPDATE': 2, 'NLRI_MP_WITHDRAW': 3, 'NLRI_TYPE_MAX': 4}
check:NLRI_UPDATE before NLRI_WITHDRAW=True
check:loop dispatches update before withdraw=True
check:withdraw parse nullifies attr=True
check:attr controls update vs withdraw=True
operation_order=bgp_update -> bgp_withdraw
final_route_present=False
rfc4271_expected_final_route_present=True
```

```text
bgpd_built=false
pytest=false
exabgp=false
passwordless_sudo=false
docker daemon: unavailable
```

## Impact

A peer can send one UPDATE that re-advertises and withdraws the same prefix. [RFC 4271](https://www.rfc-editor.org/rfc/rfc4271.html) expects the re-advertisement to win; FRR may remove the route instead. This can cause a transient or persistent Adj-RIB-In/RIB state difference from compliant receivers.

## Fix Direction

Before processing withdrawn NLRI, remove any prefix that also appears in reachable NLRI for the same AFI/SAFI and AddPath context, or process withdrawals first and skip withdrawals duplicated by reachable NLRI in the same UPDATE. Add a topotest that sends one UPDATE with the same IPv4 unicast prefix in both fields and asserts that the route remains installed.
