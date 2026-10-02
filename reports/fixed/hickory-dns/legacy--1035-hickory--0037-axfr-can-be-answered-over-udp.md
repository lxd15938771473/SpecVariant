# AXFR can be answered over UDP

## Standard

[RFC 1035](https://www.rfc-editor.org/rfc/rfc1035.html) requires zone refresh/transfer to use a reliable virtual circuit and forbids UDP for zone transfers:

Standard references: [RFC 1035 Section 4.2](https://www.rfc-editor.org/rfc/rfc1035.html#section-4.2); [RFC 1035 Section 4.2.1](https://www.rfc-editor.org/rfc/rfc1035.html#section-4.2.1).

```text
The DNS assumes that messages will be transmitted as datagrams or in a
byte stream carried by a virtual circuit. ...
Zone refresh activities must use virtual circuits because of the need for reliable transfer.

UDP is not acceptable for zone transfers, but is the recommended method
for standard queries in the Internet.
```

[RFC 5936 Section 4](https://www.rfc-editor.org/rfc/rfc5936.html#section-4) keeps the same rule for AXFR:

```text
AXFR sessions are currently restricted to TCP ...
Because accuracy is essential, TCP or some other reliable protocol
must be used for AXFR requests.
```

## Code

UDP requests enter the normal request path with `Protocol::Udp`:

```rust
// crates/server/src/server/mod.rs:486-490
inner_join_set.spawn(async move {
    cx.handle_raw_request(message, Protocol::Udp, stream_handle)
        .await;
});
```

The protocol is stored on the request:

```rust
// crates/server/src/server/mod.rs:778-786
let request = match MessageRequest::read_with_queries(&mut decoder, queries.clone(), header) {
    Ok(message) => Request {
        message,
        raw: message_bytes,
        src: src_addr,
        protocol,
    },
```

But AXFR routing checks only the query type:

```rust
// crates/server/src/zone_handler/catalog.rs:422-431
if request_info.query.query_type() == RecordType::AXFR {
    zone_transfer(
        request_info,
        handlers,
        request,
        response_edns,
        now,
        response_handle,
    )
    .await
}
```

The in-memory zone handler allows AXFR when `AxfrPolicy::AllowAll` is set, without checking the request protocol:

```rust
// crates/server/src/store/in_memory/mod.rs:528-533
let request_info = request.request_info();
if request_info.query.query_type() == RecordType::AXFR {
    if !matches!(self.axfr_policy, AxfrPolicy::AllowAll) {
        return Some((Err(LookupError::from(ResponseCode::Refused)), None));
    }
}
```

Then it returns a `ZoneTransfer`, and `catalog::zone_transfer` builds and sends a response:

```rust
// crates/server/src/store/in_memory/mod.rs:555-565
let records = AxfrRecords::new(
    lookup_options.dnssec_ok,
    self.inner.read().await.records.values().cloned().collect(),
);

Some((Ok(ZoneTransfer { start_soa, records, end_soa }), None))
```

```rust
// crates/server/src/zone_handler/catalog.rs:640-645, 664-720
let zone_transfer = match result {
    Ok(zone_transfer) => {
        response_meta.response_code = ResponseCode::NoError;
        response_meta.authoritative = true;
        Some(zone_transfer)
    }
...
match response_handle.send_response(message_response).await {
    Ok(response_info) => return response_info,
```

## Runtime Evidence

The existing integration test `test_axfr_allow_all` constructed an AXFR Request with `Protocol::Udp`, passed it through the catalog with transfers allowed, and asserted that the returned answer began and ended with SOA records. The recorded command was `cargo test -p hickory-integration --test integration test_axfr_allow_all -- --nocapture`. It passed with one test run and 78 filtered out. The excerpt below shows the UDP protocol tag and SOA-boundary assertions. This exercises server response construction for a UDP-tagged request; it does not by itself constitute a packet capture of a complete transfer.

Recorded inputs, output, and checks:

```rust
// tests/integration-tests/tests/integration/catalog_tests.rs:466-484
let question_req =
    Request::from_bytes(question_bytes, ([127, 0, 0, 1], 5553).into(), Protocol::Udp).unwrap();
...
let result = response_handler.into_message().await;
let mut answers = result.answers;
assert_eq!(answers.first().expect("no records found?"), &soa);
assert_eq!(answers.last().expect("no records found?"), &soa);
```

```text
running 1 test
test catalog_tests::test_axfr_allow_all ... ok

test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 78 filtered out
```

## Decision

[RFC 1035](https://www.rfc-editor.org/rfc/rfc1035.html) and [RFC 5936](https://www.rfc-editor.org/rfc/rfc5936.html) forbid UDP AXFR, but Hickory routes `QTYPE=AXFR` to zone transfer without a TCP/reliable-transport guard. With `AxfrPolicy::AllowAll`, a UDP AXFR request can receive zone-transfer records.
