# Unrecognized well-known attributes are treated as withdraw

## Summary

For an established BGP session, an UPDATE carrying an unrecognized well-known/non-optional attribute should be rejected with `NOTIFICATION(Update Message Error, Unrecognized Well-known Attribute = 2)` and include the offending attribute in the Data field. FRR instead routes this path to [RFC 7606](https://www.rfc-editor.org/rfc/rfc7606.html)-style `treat-as-withdraw`, so no subcode 2 NOTIFICATION is sent.

## Standard Requirement

[RFC 4271 Section 6.3](https://www.rfc-editor.org/rfc/rfc4271.html#section-6.3) defines the error:

```text
If any of the well-known mandatory attributes are not recognized,
then the Error Subcode MUST be set to Unrecognized Well-known
Attribute. The Data field MUST contain the unrecognized attribute
(type, length, and value).
```

The UPDATE subcode table gives:

```text
2 - Unrecognized Well-known Attribute.
```

[RFC 7606](https://www.rfc-editor.org/rfc/rfc7606.html) updates [RFC 4271](https://www.rfc-editor.org/rfc/rfc4271.html) UPDATE error handling, but only for specified cases such as attribute flag conflicts, missing well-known attributes, malformed `ORIGIN`, `AS_PATH`, `NEXT_HOP`, `MULTI_EXIT_DISC`, `LOCAL_PREF`, and selected optional attributes. It does not replace the [RFC 4271](https://www.rfc-editor.org/rfc/rfc4271.html) rule for unrecognized well-known mandatory attributes. Its general advice that `treat-as-withdraw` is preferred is guidance, not a blanket override for this subcode.

References:
- [RFC 4271](https://www.rfc-editor.org/rfc/rfc4271.html): [RFC 4271 Section 4.5](https://www.rfc-editor.org/rfc/rfc4271.html#section-4.5), [RFC 4271 Section 6.3](https://www.rfc-editor.org/rfc/rfc4271.html#section-6.3)
- [RFC 7606](https://www.rfc-editor.org/rfc/rfc7606.html): [RFC 7606](https://www.rfc-editor.org/rfc/rfc7606.html), sections 2, 3, 7, 8

## Relevant Source Code

FRR defines the correct subcode:

```c
/* bgpd/bgpd.h:2499-2502 */
#define BGP_NOTIFY_UPDATE_MAL_ATTR               1
#define BGP_NOTIFY_UPDATE_UNREC_ATTR             2
#define BGP_NOTIFY_UPDATE_MISS_ATTR              3
```

The unknown non-optional path passes that subcode into `bgp_attr_malformed()`:

```c
/* bgpd/bgp_attr.c:4337-4343 */
if (!CHECK_FLAG(flag, BGP_ATTR_FLAG_OPTIONAL)) {
	return bgp_attr_malformed(args, BGP_NOTIFY_UPDATE_UNREC_ATTR,
				  args->total);
}
```

But `bgp_attr_malformed()` maps default unknown attributes to withdraw, not notification:

```c
/* bgpd/bgp_attr.c:1950-1964 */
default:
	/* Unknown attributes ... should be treated as withdraw ... */
	flog_err(EC_BGP_ATTR_FLAG,
		 "%s(%u) attribute received, while it is not known how to handle it, treating as withdraw",
		 lookup_msg(attr_str, args->type, NULL), args->type);
	break;

return BGP_ATTR_PARSE_WITHDRAW;
```

`bgp_update_receive()` only stops the session on `BGP_ATTR_PARSE_ERROR`; withdraw continues through NLRI parsing with `NULL` attributes:

```c
/* bgpd/bgp_packet.c:2511-2518 */
attr_parse_ret = bgp_attr_parse(connection, &attr, attribute_len,
				&nlris[NLRI_MP_UPDATE], &nlris[NLRI_MP_WITHDRAW],
				update_len > 0);
if (attr_parse_ret == BGP_ATTR_PARSE_ERROR) {
	bgp_attr_unintern_sub(&attr);
	return BGP_Stop;
}
```

```c
/* bgpd/bgp_packet.c:2502-2506 */
#define NLRI_ATTR_ARG \
	((attr_parse_ret != BGP_ATTR_PARSE_WITHDRAW && \
	  attr_parse_ret != BGP_ATTR_PARSE_WITHDRAW_IGNORE) \
		 ? &attr : NULL)
```

## Implementation Behavior

Trigger: established session receives an UPDATE containing an unknown path attribute with the Optional bit cleared, for example flags `0x40`.

Observed source path:

1. `bgp_attr_parse()` dispatches the unknown type to `bgp_attr_unknown()`.
2. `bgp_attr_unknown()` recognizes Optional bit = 0 and calls `bgp_attr_malformed(..., BGP_NOTIFY_UPDATE_UNREC_ATTR, ...)`.
3. `bgp_attr_malformed()` does not send `BGP_NOTIFY_UPDATE_ERR / 2`; for the default unknown case it returns `BGP_ATTR_PARSE_WITHDRAW`.
4. `bgp_update_receive()` handles that as route withdrawal and does not return `BGP_Stop`.

## Inconsistency Reason

[RFC 4271](https://www.rfc-editor.org/rfc/rfc4271.html) requires the error subcode for an unrecognized well-known mandatory attribute to be `2` and requires the Data field to contain the unrecognized attribute. [RFC 7606](https://www.rfc-editor.org/rfc/rfc7606.html) does not revise this exact case. FRR has the correct constant and initially selects it, but the shared malformed-attribute handler converts the case into `treat-as-withdraw` before any NOTIFICATION can be sent.

## Runtime Evidence

An executable source-path probe followed handling of an unrecognized well-known attribute and reported treat-as-withdraw instead of NOTIFICATION subcode 2. This verified the inspected decision path, without injecting an UPDATE into a running daemon. Live-test attempts did not complete: Windows pytest collection failed because the Unix resource module was unavailable, WSL lacked pytest, and the checked FRR tree contained no built bgpd, zebra, or vtysh. Those environment failures provide no runtime observation of FRR's protocol handling.

Recorded output and checks (local artifact paths omitted):

```text
observed_behavior: treat-as-withdraw, not NOTIFICATION subcode 2
```

## Impact

A peer that sends an unrecognized well-known attribute is not reset with the mandated UPDATE error subcode. Operators and peers may see route withdrawal behavior instead of the protocol-specified notification reason.

## Fix Direction

Handle `BGP_NOTIFY_UPDATE_UNREC_ATTR` as a session-reset case in `bgp_attr_malformed()` or before calling it from `bgp_attr_unknown()`, sending `BGP_NOTIFY_UPDATE_ERR` with subcode `BGP_NOTIFY_UPDATE_UNREC_ATTR` and the offending attribute data.
