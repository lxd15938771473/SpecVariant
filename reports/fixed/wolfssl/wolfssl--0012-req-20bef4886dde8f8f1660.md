# TLS session ticket partition key prefix aliasing in wolfSSL client cache

## Summary

This is a real issue. RFC 9846 Appendix C.4 says client applications should not offer tickets across connections that are meant to be uncorrelated. wolfSSL provides `wolfSSL_SetServerID()` so applications can separate the client session and ticket cache with a custom key. However, the client-cache lookup compares only the requested key length and does not require the cached session's original key length to be equal.

As a result, a shorter partition key can match the prefix of a longer key in the same cache row and reuse a TLS 1.3 ticket saved under the longer key. A fresh TLS 1.3 reproducer run confirmed this: after the first connection used long key `origin|Ak`, the second connection using shorter key `origin|A` resumed, and both client and server reported `reused=1`.

## Standard Requirement

Official standard links:

- [RFC 9846 Appendix C.4, Client and Server Tracking Prevention](https://datatracker.ietf.org/doc/html/rfc9846#appendix-C.4)
- [RFC 9846 Section 1.1, Conventions and Terminology](https://datatracker.ietf.org/doc/html/rfc9846#section-1.1)

Relevant meaning:

- RFC 9846 Section 1.1 binds uppercase `SHOULD NOT` to BCP 14 interpretation.
- Appendix C.4 explains that offering a ticket lets a server correlate different connections, independent of ticket reuse count.
- Therefore, client applications should not offer tickets across connections that are meant to be uncorrelated. The RFC gives web browser network partition keys as an example.

Key excerpt:

```text
Client applications SHOULD NOT offer tickets across
connections that are meant to be uncorrelated.
```

This issue is not about the SNI or certificate resumption conditions in RFC 9846 Section 4.7.1. Section 4.7.1 controls when resumption is acceptable for server identity and certificate constraints. This report concerns the privacy partitioning rule in Appendix C.4: the application supplied different partition keys, but the library mixed them and caused a ticket to be offered and accepted across partitions.

## Relevant Source Code

Target source tree: `implementions/wolfssl-master`

`src/ssl_sess.c:331-374` shows `wolfSSL_SetServerID()` receiving the application-provided `serverID` key. When `newSession == 0`, it first calls `wolfSSL_GetSessionClient()` to look for an existing client session. If none is found, it saves the current key and length in the session:

```c
if (newSession == 0) {
    session = wolfSSL_GetSessionClient(ssl, id, len);
    if (session) {
        if (wolfSSL_SetSession(ssl, session) != WOLFSSL_SUCCESS) {
            WOLFSSL_MSG("wolfSSL_SetSession failed");
            session = NULL;
        }
    }
}

if (session == NULL) {
    ssl->session->idLen = (word16)len;
    XMEMCPY(ssl->session->serverID, id, (size_t)len);
}
```

`src/ssl_sess.c:1653-1727` places client-cache entries into a row computed from `serverID` and `idLen`:

```c
if (idLen) {
    clientRow = HashObject(serverID,
            idLen, &error) % CLIENT_SESSION_ROWS;
}
```

The defect is in `src/ssl_sess.c:998-1062`. The lookup computes the row using the requested key length, but the match predicate compares only `len` bytes and does not check whether `current->idLen == len`:

```c
if (current && XMEMCMP(current->serverID, id,
                                             (unsigned long)len) == 0) {
    WOLFSSL_MSG("Found a serverid match for client");
    if (LowResTimer() < (current->bornOn + current->timeout)) {
        WOLFSSL_MSG("Session valid");
        ret = current;
```

`wolfssl/internal.h:4905-4907` shows the session already stores enough information for strict matching:

```c
word16             idLen;             /* serverID length          */
byte               serverID[SERVER_ID_LEN]; /* for easier client lookup */
```

The missing check is therefore a length equality condition in the lookup predicate, not a missing field.

## Trigger Conditions

The tested build enables the affected path:

- `HAVE_SESSION_TICKET` is defined.
- `NO_MD5` is defined.
- `NO_SHA` is not defined.
- `WOLFSSL_TLS13` is defined.

With the default cache configuration, `CLIENT_SESSION_ROWS` is `88`. The reproducer uses two different application partition keys:

| Application partition key | Length | SHA1 first four bytes | Row modulo 88 | Behavior |
|---|---:|---|---:|---|
| `origin|Ak` | 9 | `aa099dcc` | 44 | first connection, full handshake |
| `origin|A` | 8 | `243b6a94` | 44 | second connection, incorrect resumption |

This is not a SHA1 collision. The two keys hash differently, but modulo 88 places both in the same client-cache row. At the same time, `origin|A` is a prefix of `origin|Ak`. Because lookup compares only the requested length, the short key accepts the cached session created for the long key.

## Runtime Evidence

Fresh rerun date: 2026-08-10.

Action: a TLS 1.3 reproducer was run from the workspace root using the ticket-enabled audit build. The reproducer first connected with partition key `origin|Ak`, then connected again with the distinct shorter prefix key `origin|A`.

Observed output:

```text
client partition=origin|Ak new_session=0 reused=0 marker=0
client partition=origin|A new_session=0 reused=1 marker=1
server connection=1 reused=0
server connection=2 reused=1
RESULT=ISSUE distinct_prefix_partition_key_reused=1
process_exit=0
```

Interpretation:

- The first connection used `origin|Ak` and did not resume, as expected for a first connection.
- The second connection used the different key `origin|A`, but resumed.
- Both client and server reported `reused=1` on the second connection, proving the old ticket was offered and accepted.
- Process exit code `0` means the reproducer observed the target defect condition.

Action: the control mode was run on the same build. It tested same-key resumption, different same-length partition keys, and an explicit fresh-session request.

Observed control output:

```text
client partition=origin.example|partition-A new_session=0 reused=0 marker=0
client partition=origin.example|partition-A new_session=0 reused=1 marker=1
client partition=origin.example|partition-B new_session=0 reused=0 marker=2
client partition=origin.example|partition-A new_session=1 reused=0 marker=3
server connection=1 reused=0
server connection=2 reused=1
server connection=3 reused=0
server connection=4 reused=0
RESULT=PASS same_partition_resumed=1 cross_partition_resumed=0 forced_fresh_resumed=0
process_exit=0
```

Interpretation:

- The same partition key can resume, so session tickets work normally.
- A different same-length partition key does not resume, so the test is not "all different keys resume".
- `newSession=1` forces a fresh session, so the application's explicit fresh-session path works.

## Impact

This is a conditional privacy partitioning and cache correctness issue. Applications that use `wolfSSL_SetServerID()` with variable-length partition keys can reuse tickets across partitions when all of these conditions hold:

- one partition key is a prefix of another partition key;
- both keys land in the same client-cache row after `HashObject()`;
- the build enables session tickets and client cache;
- the caller allows lookup of an existing session, meaning `newSession == 0`.

Fixed-length keys, explicit length-prefix encoding, or `wolfSSL_SetServerID(..., newSession=1)` avoid this specific trigger, but those are application-side workarounds rather than a library fix.

## Fix Direction

Add a length equality check to `wolfSSL_GetSessionClient()`:

```c
current->idLen == len &&
XMEMCMP(current->serverID, id, (unsigned long)len) == 0
```

Recommended regression coverage:

- long key cached first, short prefix key looked up second, both in the same client-cache row;
- short key cached first, long key looked up second;
- same row but non-prefix different keys;
- equal-length but different-content keys;
- `newSession=1` fresh-session path.
