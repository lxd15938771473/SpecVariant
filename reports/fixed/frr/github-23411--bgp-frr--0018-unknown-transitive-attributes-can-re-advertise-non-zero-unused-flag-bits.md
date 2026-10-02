# Unknown transitive attributes can re-advertise non-zero unused flag bits

## Summary

FRR masks the lower four bits of the received Attribute Flags octet when parsing, so receive-side classification ignores them. However, for an unknown optional-transitive path attribute, FRR stores the original attribute header bytes in `transit->val`, only ORs in the Partial bit, and later writes `transit->val` back to outgoing UPDATEs verbatim. Therefore a received flag such as `0xc5` can be re-advertised as `0xe5`, while [RFC 4271](https://www.rfc-editor.org/rfc/rfc4271.html) requires sent lower-order four bits to be zero.

## Standard Requirement

Official standard: [RFC 4271 Section 4.3](https://www.rfc-editor.org/rfc/rfc4271.html#section-4.3), UPDATE Message Format, [RFC 4271 Section 4.3](https://www.rfc-editor.org/rfc/rfc4271.html#section-4.3)

```text
The lower-order four bits of the Attribute Flags octet are
unused.  They MUST be zero when sent and MUST be ignored when
received.
```

[RFC 4271](https://www.rfc-editor.org/rfc/rfc4271.html) also requires accepted unrecognized transitive optional attributes to be passed along with the Partial bit set:

```text
If a path with an unrecognized transitive optional attribute is accepted
and passed to other BGP peers, then the unrecognized transitive
optional attribute of that path MUST be passed, along with the path,
to other BGP peers with the Partial bit in the Attribute Flags octet
set to 1.
```

So the implementation may ignore the unused bits on receive, but any forwarded/sent attribute still has to carry zero in those four bits.

## Relevant Source Code

`implementions/frr-master/frr-master/bgpd/bgp_attr.c:4457-4489`

```c
startp = BGP_INPUT_PNT(connection);

/*
 * The lower-order four bits of the Attribute Flags octet are
 * unused. They MUST be zero when sent and MUST be ignored when
 * received.
 */
flag = CHECK_FLAG(0xF0, stream_getc(BGP_INPUT(connection)));
type = stream_getc(BGP_INPUT(connection));
```

`startp` points at the original received attribute header. `flag` is masked for parsing, but the original byte remains available through `startp`.

`implementions/frr-master/frr-master/bgpd/bgp_attr.c:4569-4577`

```c
struct bgp_attr_parser_args attr_args = {
	.connection = connection,
	.length = length,
	.attr = attr,
	.type = type,
	.flags = flag,
	.startp = startp,
	.total = attr_endp - startp
};
```

The unknown-attribute handler receives both the masked `flags` and the original `startp`.

`implementions/frr-master/frr-master/bgpd/bgp_attr.c:4341-4367`

```c
if (!CHECK_FLAG(flag, BGP_ATTR_FLAG_OPTIONAL)) {
	return bgp_attr_malformed(args, BGP_NOTIFY_UPDATE_UNREC_ATTR,
				  args->total);
}

if (!CHECK_FLAG(flag, BGP_ATTR_FLAG_TRANS))
	return BGP_ATTR_PARSE_PROCEED;

SET_FLAG(*startp, BGP_ATTR_FLAG_PARTIAL);

transit = bgp_attr_get_transit(attr);
if (!transit)
	transit = XCALLOC(MTYPE_TRANSIT, sizeof(struct transit));

transit->val = XREALLOC(MTYPE_TRANSIT_VAL, transit->val,
			transit->length + total);

memcpy(transit->val + transit->length, startp, total);
transit->length += total;
bgp_attr_set_transit(attr, transit);
```

For unknown optional-transitive attributes, FRR sets Partial on the original header byte and copies that original byte sequence into transit storage. There is no `& 0xF0` or equivalent cleanup before `memcpy`.

`implementions/frr-master/frr-master/lib/zebra.h:213-216`

```c
#define CHECK_FLAG(V,F)      ((V) & (F))
#define SET_FLAG(V,F)        (V) |= (F)
#define UNSET_FLAG(V,F)      (V) &= ~(F)
```

`SET_FLAG(*startp, BGP_ATTR_FLAG_PARTIAL)` is an OR operation; it does not clear already-present low-order bits.

`implementions/frr-master/frr-master/bgpd/bgp_attr.c:6131-6135`

```c
/* Unknown transit attribute. */
struct transit *transit = bgp_attr_get_transit(attr);

if (transit)
	stream_put(s, transit->val, transit->length);
```

Outbound UPDATE encoding writes the stored unknown transitive attributes verbatim.

## Implementation Behavior

Receive-side behavior is partially correct: the parser uses `flag = received & 0xF0`, so validation and dispatch ignore the lower four bits.

Forwarding behavior is not compliant: the unknown optional-transitive path copies the original received header into `transit->val` after only setting Partial. If the peer sent unused bits as non-zero, those bits remain in the first octet and are later emitted unchanged by `stream_put`.

## Inconsistency Reason

The standard combines two requirements: ignore unused lower bits when receiving, and send those bits as zero. FRR satisfies the receive-side masking for parser decisions, but its unknown-transitive forwarding path reuses the received byte as the outbound byte. This preserves bits that must be zero when sent.

## Runtime Evidence

The source-observation harness was run in control and reproducer modes, followed by a byte-level model of unknown-transitive-attribute handling. The focused input was `c5 fa 01 99`: an optional transitive unknown attribute with nonzero unused flag bits. The receive mask produced 0xc0, but adding Partial to the retained original bytes produced 0xe5 rather than the compliant 0xe0. Thus the model preserved the unused low nibble in its outbound bytes. No bgpd binary was available in the checked tree, so no live-daemon forwarding was observed.

Recorded output and checks (local artifact paths omitted):

```json
{
  "input_attribute_hex": "c5 fa 01 99",
  "parsed_flag_after_receive_mask": "0xc0",
  "stored_transit_first_octet_after_set_partial": "0xe5",
  "rfc_compliant_sent_first_octet": "0xe0",
  "low_nibble_leaks_on_send": true
}
```

## Impact

A route carrying an unknown optional-transitive attribute with non-zero unused flag bits can be accepted and later re-advertised with those unused bits still non-zero. That violates [RFC 4271](https://www.rfc-editor.org/rfc/rfc4271.html)'s send-side requirement and can propagate non-canonical path attribute flags.

## Fix Direction

Normalize the stored unknown-transitive attribute header before saving or before sending it. For example, after setting Partial, clear the unused bits on `*startp`:

```c
SET_FLAG(*startp, BGP_ATTR_FLAG_PARTIAL);
*startp &= 0xF0;
```

Equivalent normalization at outbound encoding would also satisfy the send-side requirement, but normalizing before `transit->val` storage avoids keeping protocol-invalid bytes in the interned attribute state.
