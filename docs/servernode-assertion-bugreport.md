# Bug report: `ServerNode.cpp` assertion failures under high connection churn (v5.22)

**Project:** TeamTalk5 server (`tt5srv`)
**Version:** v5.22.0.5198 (Linux, x86_64)
**File:** `TeamTalkLib/teamtalk/server/ServerNode.cpp`, lines 1180 and 1182
**Severity:** robustness / invariant violation under load (no crash, but server runs with broken internal state)
**Source:** built from `BearWare/TeamTalk5` (assertion path `/BearWare/TeamTalk5/Library/TeamTalkLib/teamtalk/server/ServerNode.cpp`)

## Summary

Under a high rate of short-lived TCP connections from a single peer
(loopback), `tt5srv` repeatedly fails to register a client handler with
`unable to register client handler: Invalid argument`, and each such
failure is immediately followed by two failed assertions in
`ServerNode.cpp`:

1. Line 1180 — `m_streamhandles.find(h) != m_streamhandles.end()`
   (the stream handle for the connection is not in the stream-handle map)
2. Line 1182 — `user.get()` (the `user` shared pointer is null)

The two assertions always fire as a pair. The server does **not** crash
or abort — it keeps running and accepting traffic — but the invariants
are violated 2,846 times in a ~95-second window, which indicates the
error path for a failed client-handler registration is leaving the
stream/user maps in an inconsistent state rather than cleaning up
cleanly.

## Reproduction

The load is a saturation test against a **local** `tt5srv` the reporter
owns — a ramped TCP junk-data + UDP junk-datagram flood from loopback
with a matched SDK probe pair measuring service impact. It is not a
deployed DoS; it is a capacity test on the reporter's own machine.

1. Start `tt5srv` on `127.0.0.1:10333`:
   `tt5srv -nd -wd "$PWD"`
2. Drive it with a high connection churn rate: thousands of short-lived
   TCP connections per second from loopback, sustained for ~60–90 s.
   In the reporter's run the load stepped up to ~1024 concurrent
   flood threads, producing ~4,526 TCP connects and user IDs up to
   `#3992` in the session.
3. Observe `tt5srv.log`.

## Expected behavior

When a client handler cannot be registered, the server should log the
failure, release any half-registered state for that connection, and
continue — without tripping internal invariants. The stream handle
should either not have been inserted into `m_streamhandles`, or should
be removed on the error path; the `user` object should not be
dereferenced when it can be null.

## Actual behavior

`tt5srv.log` shows, for every failed registration, the sequence:

```
unable to register client handler: Invalid argument
Failed assertion m_streamhandles.find(h) != m_streamhandles.end() in file .../ServerNode.cpp at line 1180
Failed assertion user.get() in file .../ServerNode.cpp at line 1182
```

Representative excerpt (timestamps verbatim):

```
2026-09-04 21:15:38.764470 User #2957 TCP address: 127.0.0.1 connected.
2026-09-04 21:15:38.764502 User #2958 TCP address: 127.0.0.1 connected.
2026-09-04 21:15:38.764520 User #2959 TCP address: 127.0.0.1 connected.
2026-09-04 21:15:38.764537 User #2960 TCP address: 127.0.0.1 connected.
unable to register client handler: Invalid argument
2026-09-04 21:15:38.909551 Failed assertion m_streamhandles.find(h) != m_streamhandles.end() in file /BearWare/TeamTalk5/Library/TeamTalkLib/teamtalk/server/ServerNode.cpp at line 1180
2026-09-04 21:15:38.909557 Failed assertion user.get() in file /BearWare/TeamTalk5/Library/TeamTalkLib/teamtalk/server/ServerNode.cpp at line 1182
unable to register client handler: Invalid argument
2026-09-04 21:15:38.973762 Failed assertion m_streamhandles.find(h) != m_streamhandles.end() in file /BearWare/TeamTalk5/Library/TeamTalkLib/teamtalk/server/ServerNode.cpp at line 1180
2026-09-04 21:15:38.973769 Failed assertion user.get() in file /BearWare/TeamTalk5/Library/TeamTalkLib/teamtalk/server/ServerNode.cpp at line 1182
```

## Measurements (single ~95 s window at saturation)

| Metric | Value |
| --- | --- |
| `unable to register client handler: Invalid argument` | 1,423 |
| `Failed assertion ... line 1180` (stream handle) | 1,423 |
| `Failed assertion ... line 1182` (`user.get()`) | 1,423 |
| Total assertion failures | 2,846 (exactly 2 × handler failures) |
| Assertion window | 21:15:38.909 → 21:17:13.720 (~95 s) |
| Highest user ID in session | `#3992` |
| TCP connects in session | ~4,526 |
| Segfault / abort / core dump | 0 |
| Clean `Stopped TeamTalk Server` on shutdown | yes |

The 1:1:1 correspondence (handler failure → line 1180 → line 1182)
confirms a single error path is responsible: a failed client-handler
registration is followed by a stream-handle lookup and a user lookup
that both miss, and the assertions fire instead of the error path
bailing out cleanly.

## Impact

- **No crash:** the process stays alive, the port stays bound, and the
  server recovers once the connection rate drops (a post-flood SDK
  probe reconnected with ~0.7 ms connect / ~41 ms message round-trip).
  So this is not a remote crash primitive.
- **Invariant breakage:** the stream-handle and user maps are being
  accessed in a state the code asserts is impossible, which means the
  error path is skipping cleanup. Under sustained churn the server
  becomes unreachable for new probes (0/33 service probes succeeded at
  the peak stage), consistent with the handler-registration exhaustion.
- **Resource exhaustion surface:** the `Invalid argument` from client
  handler registration suggests an underlying limit (epoll/fd/table)
  is being hit; the assertions are a symptom of the error path not
  degrading gracefully past that limit.

## Suggested investigation

- In the code path that logs `unable to register client handler`,
  confirm whether the connection's stream handle has already been
  inserted into `m_streamhandles` before registration fails. If it
  has, the error path should remove it; if it has not, the lookup at
  line 1180 should be skipped on the failure path.
- At line 1182, guard the `user` dereference against null (the
  assertion already proves it can be null on this path) and treat it
  as an expected error rather than an invariant.
- Consider whether the `Invalid argument` from handler registration is
  an `epoll_ctl`/fd-limit condition that could be surfaced earlier
  (and load-shedded) before the stream/user bookkeeping is touched.

## Environment

- Server: `tt5srv` v5.22.0.5198, Linux x86_64, Arch Linux
- Bind: `127.0.0.1:10333`, started with `tt5srv -nd -wd <dir>`
- Load source: local loopback only (reporter's own saturation test)
- Log file: `tt5srv.log` (server's own log, ~2 MB for the session)

## Attachments available on request

- The full `tt5srv.log` for the saturation session.
- The ramp/breaking-point test tool and its stage-by-stage probe
  results (open source, in the reporter's test suite).