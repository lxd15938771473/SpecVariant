# iBGP AS_PATH modification by outbound policy

## Standard

[RFC 4271](https://www.rfc-editor.org/rfc/rfc4271.html) says that when a BGP speaker propagates a route learned from another BGP speaker, AS_PATH handling depends on the destination peer. For an internal peer, modifying AS_PATH is forbidden.

[RFC 4271 Section 5.1.2](https://www.rfc-editor.org/rfc/rfc4271.html#section-5.1.2)

```text
When a BGP speaker propagates a route it learned from another BGP
speaker's UPDATE message, it modifies the route's AS_PATH attribute
based on the location of the BGP speaker to which the route will be
sent:

   a) When a given BGP speaker advertises the route to an internal
      peer, the advertising speaker SHALL NOT modify the AS_PATH
      attribute associated with the route.
```

The extracted requirement has no required action and one forbidden action:

```json
{
"condition": "When advertising to an internal peer a route learned from another BGP speaker",
"required_behavior": "",
"forbidden_behavior": "Modify the AS_PATH attribute associated with the route."
}
```

## Code

Default iBGP serialization preserves the AS_PATH already present in `attr`.

`implementions/frr-master/frr-master/bgpd/bgp_attr.c:5664-5676`

```c
} else if (!for_bmp && peer->sort == BGP_PEER_CONFED) {
	aspath = aspath_dup(attr->aspath);
	aspath = aspath_add_confed_seq(aspath, peer->local_as);
} else
	aspath = attr->aspath;
```

The problem occurs before serialization. Outbound route-map policy is applied to the announcement `attr`. The temporary-attribute guard only covers iBGP-to-iBGP reflection when RR outbound policy is not allowed.

`implementions/frr-master/frr-master/bgpd/bgp_route.c:2942-2974`

```c
if (!post_attr &&
    (ROUTE_MAP_OUT_NAME(filter) || bgp_path_suppressed(pi))) {
	prep_for_rmap_apply(&rmap_path, &dummy_rmap_path_extra, dest, pi, peer, from, attr);

	if ((from->sort == BGP_PEER_IBGP && peer->sort == BGP_PEER_IBGP)
	    && !CHECK_FLAG(bgp->flags, BGP_FLAG_RR_ALLOW_OUTBOUND_POLICY)) {
		bgp_attr_dup_into(&dummy_attr, attr);
		rmap_path.attr = &dummy_attr;
	}

	ret = route_map_apply(ROUTE_MAP_OUT(filter), p, &rmap_path);
}
```

For an EBGP-learned route advertised to an iBGP peer, that guard does not fire. `set as-path prepend` then writes the changed AS_PATH back to the outbound attribute.

`implementions/frr-master/frr-master/bgpd/bgp_routemap.c:2599-2623`

```c
static enum route_map_cmd_result_t
route_set_aspath_prepend(void *rule, const struct prefix *prefix, void *object)
{
	struct bgp_path_info *path;

	path = object;
	...
	path->attr->aspath = new;

	return RMAP_OKAY;
}
```

The modified `pattr` is queued and encoded into the UPDATE.

`implementions/frr-master/frr-master/bgpd/bgp_route.c:4016-4026`

```c
if (subgroup_announce_check(dest, selected, subgrp, p, pattr, NULL)) {
	...
	bgp_adj_out_set_subgroup(dest, subgrp, pattr, selected)
```

`implementions/frr-master/frr-master/bgpd/bgp_updgrp_adv.c:595-656`

```c
attr_new = bgp_attr_intern(attr);
...
adv->baa = bgp_advertise_attr_intern(subgrp->hash, attr_new);
```

`implementions/frr-master/frr-master/bgpd/bgp_updgrp_packet.c:786-789`

```c
total_attr_len = bgp_packet_attribute(NULL, peer, s, adv->baa->attr,
				      &vecarr, NULL, afi, safi, from, NULL,
				      NULL, 0, dest->srv6_unicast, 0, 0,
				      path, NULL, false);
```

## Runtime Evidence

The source-observation harness checked the baseline and error-mapping variants in control and reproducer modes. Both controls returned ok=true and both reproducers returned ok=true. A compiled branch model then applied outbound AS-path prepend to path 64512. An eBGP-learned route sent to iBGP became 65000 64512; a reflected iBGP route stayed 64512 without the route-reflector policy override and became 65000 64512 with it. The output label modified_on_wire describes the model's encoded path; no live-daemon wire capture was recorded.

Recorded output and checks (local artifact paths omitted):

```text
baseline control: ok=true
baseline reproducer: ok=true
error-mapping control: ok=true
error-mapping reproducer: ok=true
```

```text
Outbound route-map `set as-path prepend` can run before iBGP UPDATE
serialization and change path->attr->aspath, contradicting the SHALL NOT
on modifying AS_PATH for an internal-peer advertisement.
```

```text
EBGP learned -> iBGP with outbound prepend: encoded_as_path='65000 64512' modified_on_wire=yes
iBGP reflected -> iBGP without RR allow: encoded_as_path='64512' modified_on_wire=no
iBGP reflected -> iBGP with RR allow: encoded_as_path='65000 64512' modified_on_wire=yes
RESULT: branch behavior matches FRR source evidence
```

## Decision

This is a real issue. FRR's default iBGP serializer preserves AS_PATH, but outbound `set as-path prepend` can modify AS_PATH before a learned route is advertised to an internal peer. That conflicts with [RFC 4271](https://www.rfc-editor.org/rfc/rfc4271.html)'s `SHALL NOT modify the AS_PATH` rule.
