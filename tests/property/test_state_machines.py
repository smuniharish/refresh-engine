from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

from hypothesis import event, settings
from hypothesis import strategies as st
from hypothesis.stateful import (
    Bundle,
    RuleBasedStateMachine,
    initialize,
    invariant,
    precondition,
    rule,
)

from refresh_engine import (
    ContentHash,
    InMemoryStateStore,
    PlanAction,
    RefreshConfig,
    RefreshEngine,
    RefreshMode,
    RefreshResult,
    RefreshStatus,
    ResourceFingerprint,
    ResourceState,
    StateTransactionABC,
    canonical_digest,
)
from tests.support import FakeSource, RecordingOperation

IDS = ("a", "b", "c", "d", "e")
RETRY = "retry"

type Stored = tuple[str, frozenset[str]]
type Node = tuple[PlanAction, str]


class StoreMachine(RuleBasedStateMachine):
    """InMemoryStateStore behaves like a dictionary with atomic batches."""

    transactions = Bundle("transactions")

    def __init__(self) -> None:
        super().__init__()
        self.loop = asyncio.new_event_loop()
        self.store = InMemoryStateStore()
        self.model: dict[str, ResourceState] = {}
        self.staged: dict[int, tuple[dict[str, ResourceState], set[str]]] = {}
        self.handles: dict[int, StateTransactionABC] = {}

    def run[T](self, coroutine: Coroutine[Any, Any, T]) -> T:
        return self.loop.run_until_complete(coroutine)

    @rule(target=transactions)
    def begin(self) -> int:
        handle = len(self.handles)
        transaction = self.store.transaction()
        assert isinstance(transaction, StateTransactionABC)
        self.handles[handle] = transaction
        self.staged[handle] = ({}, set())
        return handle

    @precondition(lambda self: bool(self.staged))
    @rule(
        handle=transactions,
        resource_id=st.sampled_from(IDS),
        value=st.integers(0, 3),
    )
    def put(self, handle: int, resource_id: str, value: int) -> None:
        if handle not in self.staged:
            return
        state = ResourceState(resource_id, ResourceFingerprint("test", str(value)))
        self.run(self.handles[handle].put(state))
        puts, deletes = self.staged[handle]
        puts[resource_id] = state
        deletes.discard(resource_id)

    @precondition(lambda self: bool(self.staged))
    @rule(handle=transactions, resource_id=st.sampled_from(IDS))
    def delete(self, handle: int, resource_id: str) -> None:
        if handle not in self.staged:
            return
        self.run(self.handles[handle].delete(resource_id))
        puts, deletes = self.staged[handle]
        puts.pop(resource_id, None)
        deletes.add(resource_id)

    @precondition(lambda self: bool(self.staged))
    @rule(handle=transactions)
    def commit(self, handle: int) -> None:
        if handle not in self.staged:
            return
        before = self.run(self.store.load_all())
        snapshot = dict(before)
        self.run(self.handles[handle].commit())
        puts, deletes = self.staged.pop(handle)
        for resource_id in deletes:
            self.model.pop(resource_id, None)
        self.model.update(puts)
        assert dict(before) == snapshot

    @precondition(lambda self: bool(self.staged))
    @rule(handle=transactions)
    def rollback(self, handle: int) -> None:
        if handle not in self.staged:
            return
        self.run(self.handles[handle].rollback())
        del self.staged[handle]

    @invariant()
    def store_matches_model(self) -> None:
        assert dict(self.run(self.store.load_all())) == self.model

    def teardown(self) -> None:
        self.loop.close()


class EngineMachine(RuleBasedStateMachine):
    """The engine agrees with an independent model of refresh semantics."""

    def __init__(self) -> None:
        super().__init__()
        self.loop = asyncio.new_event_loop()
        self.values: dict[str, int] = {}
        self.dependencies: dict[str, frozenset[str]] = {}
        self.complete = True
        self.failing_operations: set[str] = set()
        self.failing_snapshots: set[str] = set()
        self.stored: dict[str, Stored] = {}
        self.cheap = False
        self.source = FakeSource({})
        self.operation = RecordingOperation()
        self.engine = RefreshEngine(self.source, self.operation)

    @initialize(cheap=st.booleans(), concurrency=st.integers(1, 3))
    def configure(self, cheap: bool, concurrency: int) -> None:
        self.cheap = cheap
        self.source = FakeSource({}, cheap=cheap)
        self.engine = RefreshEngine(
            self.source,
            self.operation,
            fingerprint=ContentHash(),
            config=RefreshConfig(max_concurrency=concurrency),
        )

    @rule(resource_id=st.sampled_from(IDS), value=st.integers(0, 2))
    def set_value(self, resource_id: str, value: int) -> None:
        self.values[resource_id] = value
        self.dependencies.setdefault(resource_id, frozenset())

    @rule(resource_id=st.sampled_from(IDS))
    def remove(self, resource_id: str) -> None:
        self.values.pop(resource_id, None)

    @rule(
        resource_id=st.sampled_from(IDS),
        dependencies=st.frozensets(st.sampled_from(IDS), max_size=2),
    )
    def set_dependencies(self, resource_id: str, dependencies: frozenset[str]) -> None:
        self.dependencies[resource_id] = dependencies

    @rule(complete=st.booleans())
    def set_complete(self, complete: bool) -> None:
        self.complete = complete

    @rule(resource_id=st.sampled_from(IDS))
    def toggle_operation_failure(self, resource_id: str) -> None:
        self.failing_operations ^= {resource_id}

    @rule(resource_id=st.sampled_from(IDS))
    def toggle_snapshot_failure(self, resource_id: str) -> None:
        self.failing_snapshots ^= {resource_id}

    @rule(
        mode=st.sampled_from(
            [RefreshMode.INCREMENTAL, RefreshMode.FULL, RefreshMode.DEPENDENCY_AWARE]
        )
    )
    def refresh(self, mode: RefreshMode) -> None:
        self.check(mode, frozenset())

    @rule(selection=st.frozensets(st.sampled_from(IDS), min_size=1))
    def refresh_targets(self, selection: frozenset[str]) -> None:
        self.check(RefreshMode.TARGETED, selection)

    def check(self, mode: RefreshMode, targets: frozenset[str]) -> None:
        source = self.source
        source.values = dict(self.values)
        source.dependencies = {
            name: set(self.dependencies.get(name, frozenset())) for name in self.values
        }
        source.complete = self.complete
        source.snapshot_errors = {
            name: OSError("read") for name in self.failing_snapshots
        }
        self.operation.failures = {
            name: RuntimeError("write") for name in self.failing_operations
        }
        self.operation.calls.clear()
        expected = self.expect(mode, targets)
        result = self.loop.run_until_complete(
            self.engine.refresh(mode=mode, resource_ids=targets)
        )
        self.verify(expected, result)

    def digest(self, name: str) -> str:
        return canonical_digest(self.values[name])

    def deps(self, name: str) -> frozenset[str]:
        return self.dependencies.get(name, frozenset())

    def expect(self, mode: RefreshMode, targets: frozenset[str]) -> dict[str, Any]:
        discovered = set(self.values)
        scope = discovered & targets if mode is RefreshMode.TARGETED else discovered
        forced = mode in (RefreshMode.FULL, RefreshMode.TARGETED)
        cheap = {
            name
            for name in scope
            if self.cheap
            and not forced
            and name in self.stored
            and self.stored[name][0] == self.digest(name)
        }
        unknown = (scope - cheap) & self.failing_snapshots
        observed = scope - unknown
        changes: dict[str, str] = {}
        for name in observed:
            stored = self.stored.get(name)
            if stored is None:
                changes[name] = "added"
            elif stored[0] != self.digest(name):
                changes[name] = "modified"
            elif stored[1] != self.deps(name):
                changes[name] = "dependency_changed"
            else:
                changes[name] = "unchanged"
        if mode is RefreshMode.TARGETED:
            candidates = {
                name
                for name in targets
                if name not in discovered and name in self.stored
            }
        else:
            candidates = set(self.stored) - discovered
        deleted = candidates if self.complete else set()
        changed = {name for name, change in changes.items() if change != "unchanged"}
        impacted: set[str] = set()
        if forced:
            refresh = set(observed)
        elif mode is RefreshMode.INCREMENTAL:
            refresh = set(changed)
        else:
            effective = {name: item[1] for name, item in self.stored.items()}
            effective.update({name: self.deps(name) for name in observed})
            reached = changed | deleted
            grew = True
            while grew:
                grew = False
                for name, needs in effective.items():
                    if name not in reached and needs & reached:
                        reached.add(name)
                        grew = True
            impacted = (reached & observed) - changed
            refresh = changed | impacted
        late = {
            name
            for name in refresh
            if not forced and (name in cheap or changes[name] == "unchanged")
        }

        nodes: set[Node] = {(PlanAction.REFRESH, name) for name in refresh}
        nodes |= {(PlanAction.DELETE, name) for name in deleted}
        edges: set[tuple[Node, Node]] = set()
        for name in refresh:
            for dependency in self.deps(name) & refresh:
                edges.add(
                    ((PlanAction.REFRESH, dependency), (PlanAction.REFRESH, name))
                )
            related = set(self.deps(name))
            if name in self.stored:
                related |= self.stored[name][1]
            for dependency in related & deleted:
                edges.add(((PlanAction.DELETE, dependency), (PlanAction.REFRESH, name)))
        for name in deleted:
            for dependency in self.stored[name][1] & deleted:
                edges.add(((PlanAction.DELETE, name), (PlanAction.DELETE, dependency)))

        def reaches(start: Node, goal: Node) -> bool:
            frontier = [target for source, target in edges if source == start]
            seen: set[Node] = set()
            while frontier:
                node = frontier.pop()
                if node == goal:
                    return True
                if node not in seen:
                    seen.add(node)
                    frontier.extend(
                        target for source, target in edges if source == node
                    )
            return False

        cyclic = {node for node in nodes if reaches(node, node)}
        outcomes: dict[Node, str] = {}

        def outcome(node: Node) -> str:
            if node not in outcomes:
                if node in cyclic:
                    outcomes[node] = "cycle"
                elif any(
                    outcome(source) != "ok"
                    for source, target in edges
                    if target == node
                ):
                    outcomes[node] = "blocked"
                elif node[1] in late and node[1] in self.failing_snapshots:
                    outcomes[node] = "snapshot"
                elif node[1] in self.failing_operations:
                    outcomes[node] = "failed"
                else:
                    outcomes[node] = "ok"
            return outcomes[node]

        for node in nodes:
            outcome(node)
        stored = dict(self.stored)
        for (action, name), result in outcomes.items():
            if action is PlanAction.DELETE:
                if result == "ok":
                    del stored[name]
            elif result == "ok":
                stored[name] = (self.digest(name), self.deps(name))
            elif changes[name] == "unchanged":
                stored[name] = (RETRY, self.deps(name))
        failures = len(unknown) + sum(result != "ok" for result in outcomes.values())
        if failures == 0:
            status = RefreshStatus.SUCCESS
        elif failures < len(scope) + len(deleted):
            status = RefreshStatus.PARTIAL
        else:
            status = RefreshStatus.FAILED
        return {
            "calls": {
                node for node, result in outcomes.items() if result in ("ok", "failed")
            },
            "edges": edges,
            "stored": stored,
            "status": status,
            "counts": {
                "added": sum(change == "added" for change in changes.values()),
                "modified": sum(change == "modified" for change in changes.values()),
                "dependency_changed": sum(
                    change == "dependency_changed" for change in changes.values()
                ),
                "unchanged": sum(change == "unchanged" for change in changes.values()),
                "deleted": len(deleted),
                "impacted": len(impacted),
                "refreshed": sum(result == "ok" for result in outcomes.values()),
                "failed": failures,
                "skipped": len(discovered) - len(refresh) - len(unknown),
                "executions": len(nodes),
            },
        }

    def verify(self, expected: dict[str, Any], result: RefreshResult) -> None:
        event(f"status: {result.status.value}")
        for issue in result.errors:
            event(f"issue: {issue.phase.value}")
        calls = self.operation.calls
        assert len(calls) == len(set(calls))
        assert set(calls) == expected["calls"]
        position = {call: index for index, call in enumerate(calls)}
        for source, target in expected["edges"]:
            if source in position and target in position:
                assert position[source] < position[target]
        assert result.status is expected["status"], result.errors
        counts = expected["counts"]
        assert (
            result.added_count,
            result.modified_count,
            result.dependency_changed_count,
            result.unchanged_count,
            result.deleted_count,
            result.impacted_count,
            result.refreshed_count,
            result.failed_count,
            result.skipped_count,
            len(result.executions),
        ) == (
            counts["added"],
            counts["modified"],
            counts["dependency_changed"],
            counts["unchanged"],
            counts["deleted"],
            counts["impacted"],
            counts["refreshed"],
            counts["failed"],
            counts["skipped"],
            counts["executions"],
        )
        states = self.loop.run_until_complete(self.engine.store.load_all())
        actual = {
            name: (
                RETRY
                if state.fingerprint.algorithm == "refresh-engine:retry"
                else state.fingerprint.value,
                frozenset(state.dependencies),
            )
            for name, state in states.items()
        }
        assert actual == expected["stored"]
        self.stored = expected["stored"]

    def teardown(self) -> None:
        self.loop.run_until_complete(self.engine.close())
        self.loop.close()


TestStore = StoreMachine.TestCase
TestStore.settings = settings(TestStore.settings, stateful_step_count=30)
TestEngine = EngineMachine.TestCase
TestEngine.settings = settings(TestEngine.settings, stateful_step_count=30)
