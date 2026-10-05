# Serializable MVCC store with savepoints and recovery

Implement `Engine` and `Conflict` in `mvcc.py`, using only the standard library.
Conflict must be a subclass of RuntimeError. This is an in-memory engine;
filesystem I/O, threads, HTTP, and SQL are not required. Calls are sequential
but multiple transactions may be alive and interleaved.

Keys and savepoint names are nonempty strings. Stored values are integers
(arbitrarily large and signed); bool and None are invalid stored values.
Prefixes are strings, including "". Every invalid argument raises ValueError.
Closed transaction methods raise RuntimeError before inspecting their arguments.
Invalid operations leave all engine and transaction state unchanged.

Engine API:

- `Engine(initial=None)`: None or a dict of valid keys/values, copied defensively.
  Starts at version 0. `engine.version` exposes the current integer version.
- `begin()`: returns a new transaction with a snapshot of committed data and
  the current version. It sees that snapshot plus its own pending writes.
- `checkpoint()`: returns exactly `{"version": V, "data": {key: value, ...}}`,
  containing committed state only, with data keys inserted in sorted order.
- `log_since(version)`: returns a defensive copy of committed records newer
  than version, in commit order. The integer version (bool invalid) must be
  between the engine's retained base version and its current version inclusive.
- `Engine.restore(checkpoint, records)`: classmethod returning a new engine.
  Validate the whole checkpoint and log before accepting them. Checkpoint must
  have exactly version/data, nonnegative integer version, and a valid data dict.
  Records must be a list of dicts with exactly version/writes. Versions are
  contiguous integers starting at checkpoint.version + 1. Writes are nonempty
  lists of `[key, value_or_None]` pairs, with unique keys in strictly sorted
  order. None means deletion, including deletion of an absent key. Apply every
  record atomically. The restored engine retains these records; its base version
  is the checkpoint version, so older log_since requests raise ValueError.

Transaction API:

- `get(key)`: snapshot/own-write value, or None when absent. Records the read
  even when the key is absent or was written by this transaction.
- `scan(prefix="")`: dict of visible keys beginning with prefix, sorted by key.
  Records a range read even if it returns no keys. Includes own pending writes.
- `put(key, value)` and `delete(key)`: stage a write or tombstone. Repeated
  operations on a key leave just its last staged value. Return None.
- `savepoint(name)`: push a uniquely named savepoint containing pending writes.
  Duplicate live names raise ValueError. Return None.
- `rollback_to(name)`: restore that savepoint's writes, discard newer savepoints,
  and keep the named savepoint. All key/range reads remain tracked, including
  reads after the savepoint. Unknown names raise ValueError. Return None.
- `release(name)`: discard that savepoint and all newer savepoints, retaining
  pending writes and all reads. Unknown names raise ValueError. Return None.
- `commit()`: validate conflicts, close the transaction, and return the engine's
  current version. For a successful nonempty pending-write set, increment the
  version once and append exactly `{"version": V, "writes": [[key, value], ...]}`
  with sorted keys. Even writing the same value or deleting a missing key is
  a write and creates a version. A write-free commit does not create a version.
- `abort()`: discard this transaction's state and close it; return None.

Conflict policy is conservative serializable optimistic validation. A commit
raises Conflict if ANY committed write since its snapshot touches a key it
read or staged for writing, or a key matching ANY prefix it scanned. Validate
write-free commits too. Check committed write history, not only current values:
ABA changes, insert-then-delete, and same-value writes still conflict. Unrelated
writes do not conflict. A conflicting commit closes the transaction and changes
no engine state. Earlier checkpoint history is irrelevant to newly begun
transactions. Closed transactions cannot be reused, even to abort twice.

Example: begin a and b from {"x":1,"y":1}. a reads y then puts x=0;
b reads x then puts y=0. a commits as version 1; b must raise Conflict.
An empty scan of prefix "job/" must conflict if another transaction later
inserts a matching key, even if a third transaction deletes it before commit.

All returned dictionaries/lists are defensive copies. Inputs, snapshots, and
log exports must not alias mutable internal state. Implementations may use
snapshot copies or version chains; no particular internal layout is required.
Handle deterministic mixed traces and checkpoint/log round trips. Crash recovery
here means replaying exported checkpoint/log data, not physical disk durability.

Run `python -m unittest discover -s tests -v`. Preserve supplied tests and
instructions. Add contract-based tests as `tests/test_*.py` if useful. No external
dependencies, network, or subagents. Briefly explain the isolation and recovery
invariants in your final response.
