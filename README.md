# state-reconciler

Declare the state you want. The loop reads the world, finds the gap, and closes it. Then it looks again to prove the gap is gone.

[![PyPI](https://img.shields.io/pypi/v/state-reconciler.svg)](https://pypi.org/project/state-reconciler/)
[![Python versions](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-blue.svg)](https://github.com/pizgariu/state-reconciler)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Code style: Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

A reconciliation kernel with no domain baked in and nothing to install alongside it. You describe each concern as a `Step` that reads its own drift and knows how to close it. The kernel resolves the order and runs the steps, then reads reality back. A clean result is verified, not assumed. Pure standard library, zero runtime dependencies.

---

## See it work

This is `examples/git_repo_state.py`, printed verbatim. It takes a throwaway git repository from empty to fully configured, in the right order, and then proves the result held.

```
Throwaway repository: /tmp/reconciler-git-rf6bona1

Plan (what converge would do, in resolved order):
    - user.name: want 'Reconciler Bot', have unset
    - user.email: want 'bot@example.test', have unset
    - commit.gpgsign: want 'false', have unset
    - HEAD: repository has no commits yet
    - branch:feature/login: local branch is missing

First converge:
  applied this run:
    + user.name: set to 'Reconciler Bot'
    + user.email: set to 'bot@example.test'
    + commit.gpgsign: set to 'false'
    + HEAD: created the initial commit
    + branch:feature/login: created local branch
  residual: empty -> desired state verified by re-probe

Second converge (should be a clean no-op):
  applied this run: nothing
  residual: empty -> desired state verified by re-probe

Direct git inspection:
    user.name = 'Reconciler Bot'
    HEAD      = 13fde42
    branch    = feature/login
```

Three things in that output are the entire idea.

**The plan came out ordered.** The steps were handed in scrambled. A branch cannot exist before there is a commit, and a commit needs `user.name` and `user.email` first. Nobody wrote that sequence. The kernel read the dependency graph and produced it: config, then the initial commit, then the branch.

**The first converge did the work that was missing and nothing else.** Every line under "applied this run" is a real mutation of a real repository. Then the kernel probed a second time and found nothing left. That empty residual isn't bookkeeping. The loop went back to git after acting and confirmed reality now matches intent.

**The second converge did nothing, and that is the whole point.** Running the identical reconcile again applied zero changes and still verified clean. A no-op is not a wasted pass. It's the property that makes every other pass safe.

---

## What reconciliation actually is

Most code that touches the world is a script: a fixed run of imperative steps that assumes it starts from a known place. Run it twice and it breaks, or worse, it quietly does the wrong thing. Reconciliation drops that model. You keep a picture of the **desired** state and repeatedly drag the **actual** state toward it through a short feedback loop:

```
        +------------------------------------------------+
        |                                                |
        v                                                |
   +---------+       +----------+       +---------+      |
   |  WATCH  | ----> | COMPARE  | ----> |   ACT   | -----+
   |  read   |       | actual   |       | close   |
   | actual  |       |   vs     |       |  the    |
   | state   |       | desired  |       |  gap    |
   +---------+       +----------+       +---------+
                          |
                          v
                     the gap here
                    is called DRIFT
```

WATCH reads what is true right now, not what you last left behind. COMPARE holds that against what you declared and computes the difference. That difference is the **drift**: the itemized gap between reality and intent. ACT applies just enough to close it. Then the loop runs again from the top.

A few properties fall straight out of this shape, and they are what separate a reconciler from a setup script.

**Drift is expected, not exceptional.** Someone hand-edits a file. A branch gets deleted, a replica dies, config drifts during an incident. A reconciler does not try to prevent any of that. It assumes the world will wander off and treats every deviation as something to fix next pass. It does not raise an alarm.

**It is level-triggered, not edge-triggered.** A trigger does not mean "handle this one event". It means "re-check the whole state against desired, now". You never process a single delta. You ask the same question from scratch, every time. That is why you can miss a signal, double-fire a trigger, or run on a plain timer, and the answer stays correct.

**That forces idempotency, and idempotency is the reward.** Because the same unit of work may run any number of times, doing it twice has to land exactly where doing it once did. A pass that finds nothing to change and does nothing is a first-class outcome, the no-op from the second converge above. A system built this way **self-heals**: it converges toward desired over repeated cycles no matter how it was knocked off course.

If you have used a Kubernetes controller, you have already met this loop. It runs the same idea on your own Steps. For intuition, it is a thermostat that keeps re-reading the room instead of firing the furnace once. Or an immune system that patrols instead of firing once and going quiet. Reconciliation is what resilience gets built on, precisely because everything drifts eventually.

`state-reconciler` is that principle and nothing else, boiled down to a small kernel with no domain vocabulary. It has no idea what a file or a git repo is. You teach it one `Step` at a time.

---

## The building blocks

### `Step` - a unit of desired state

You subclass `Step` and answer one question: what is the gap between the world and what I want? You report that gap as drift, and you know how to close it.

```python
from state_reconciler import Step, Drift, DriftItem

class Config(Step):
    def __init__(self, key: str, want: str) -> None:
        self.key = key
        self.want = want

    def drift(self) -> list[Drift]:
        have = read_config(self.key)                  # WATCH
        if have == self.want:                         # COMPARE
            return []                                 # no gap, no drift
        return [DriftItem(self.key, f"want {self.want!r}, have {have!r}")]

    def apply(self) -> list[Drift] | None:
        write_config(self.key, self.want)             # ACT
        return self.drift()                           # honest re-read
```

`drift()` is WATCH plus COMPARE in one method, and it never mutates. Return an empty list when the world already matches. `apply()` is ACT, and it must be idempotent. The kernel calls both. You never write the loop. The git example at the top is three steps of exactly this shape.

The kernel reads only two fields out of your drift, through the `Drift` protocol: a `name` and a `message`. That is the entire contract. `DriftItem(name, message)` is the ready-made implementation and covers almost every step. Because the kernel reads nothing else, your domain stays entirely yours.

### `after` - declare dependencies, get ordering for free

A step names what must run before it with one class attribute:

```python
class InitialCommit(Step):
    after = (GitConfig,)

class LocalBranch(Step):
    after = (InitialCommit,)
```

Hand the reconciler these in any order and it sorts them into dependency waves. That is what produced the correct plan in the opening output. A dependency cycle raises `ValueError` at construction and names the steps it could not place. A dependency on a step outside the set you passed is ignored, so a subset still reconciles cleanly.

### `Reconciler` - the engine

```python
from state_reconciler import Reconciler

reconciler = Reconciler([LocalBranch(...), GitConfig(...), InitialCommit(...)])

reconciler.plan()                  # the ordered gap, no changes made
residual = reconciler.converge()   # WATCH -> COMPARE -> ACT -> re-probe

if not residual:
    print("verified clean")
```

`converge()` returns a `Residual`, a `list[Drift]` of whatever gap outlived the run. Empty means the kernel acted, probed again, and confirmed reality now matches intent. The changes made along the way live on a separate channel, `residual.applied`, which is what the examples print under "applied this run". Keeping the two apart means "what I fixed" never blurs into "what is still wrong".

### `Controller` - the loop that never ends

`converge()` is one turn of the crank. A `Controller` turns it once per tick, which is what a long-lived reconciler does.

```python
from state_reconciler import Controller

controller = Controller(reconciler, on_residual=log_gap)

for residual in controller.run(ticks):     # one converge per tick, lazily
    ...

controller.settle(ticks)                    # keep going until the first clean pass
```

Feed it any iterable of ticks: a timer, a queue of events, a fixed range. Level-triggered means the source does not matter. Each tick re-asks the whole question. The controller advances one tick per item you supply, so the clock stays yours.

---

## Choosing behavior

Every axis of behavior is a small object you swap. The defaults resolve to `Kahn`, `Serial`, `Once`, and no cancellation. Pass nothing and you get all four.

| Axis | The question it answers | Default | Alternatives |
| --- | --- | --- | --- |
| **Ordering** | Given the `after` graph, in what order do steps run? | `Kahn` (dependency waves) | `DFS` (flat post-order), `Priority(key=...)` (best-first frontier over a key), `Components` (split into independent chains) |
| **Executor** | How does an ordered group actually run? | `Serial` (one step at a time) | `Parallel` (fan each wave across a thread pool), `Pipeline` (run independent chains concurrently) |
| **Error policy** | When a step fails, stop or push on? | `OnError.FailFast` | `OnError.BestEffort` (finish the group, collect failures) |
| **Convergence** | How many apply-then-probe passes per converge? | `Once` (single pass) | `Fixpoint(max_passes=...)` (repeat until the residual stops changing by value, or a ceiling is hit) |
| **Cancellation** | When should a run abort cooperatively between steps? | `Cancellation` (never aborts) | `Deadline(seconds)` (wall-clock budget), `Flag` (manual switch), the composites `AnyOf` / `AllOf` / `Majority` that nest into a tree, or `Quorum(..., rule=...)` with `Some` / `Every` / `Most` for a custom rule |

`Parallel` and `Pipeline` are context managers, so use them in a `with` block to release the thread pool on exit.

```python
from state_reconciler import Reconciler, Parallel, Fixpoint, Deadline

with Parallel(width=8) as executor:
    reconciler = Reconciler(
        steps,
        executor=executor,
        convergence=Fixpoint(max_passes=10),
        cancellation=Deadline(seconds=30),
    )
    residual = reconciler.converge()
```

### More than converge

A `Reconciler` reads and writes state through a handful of verbs, each of which a `Step` can implement. Each fans across every step in resolved order, reversed for the teardown verbs.

- `drift()` reports the gap without touching anything.
- `plan()` is a dry-run read of the pending diff, in resolved order.
- `audit()` returns advisory findings about a concern that is already satisfied. These never trigger an apply.
- `footprint()` previews what a teardown would remove, in reverse order.
- `converge()` applies, then re-probes, returning the `Residual`.
- `prune()` is the reverse-order teardown itself, verifying the same way a converge does.

---

## Examples

The [`examples/`](examples/) directory holds three runnable, self-contained programs. Each builds a real throwaway resource, converges it, ends with an empty residual, and cleans up on the way out. Run any of them with `python examples/<name>.py`.

### `git_repo_state.py` - the flagship

Drives a real throwaway git repository through plain `subprocess` calls, over the chain `GitConfig -> InitialCommit -> LocalBranch`. The steps go in scrambled and `Kahn` resolves the order. Every `drift()` is a genuine read, every `apply()` a genuine mutation. Read it first. Its full output is at the top of this README.

### `filesystem_layout.py`

Brings a temp directory tree to a desired layout with `Directory` and `TextFile` steps, ordered so directories land before the files inside them. Then it tampers with a file behind the reconciler's back and re-converges. This is the clearest look at drift as something you recover from.

```
Tampering: overwrite config/app.toml with the wrong content
  drift now sees 1 problem(s):
    ! /tmp/reconciler-fs-0gafhd5o/config/app.toml: content does not match desired
Converge again to self-heal:
  applied this run:
    + /tmp/reconciler-fs-0gafhd5o/config/app.toml: wrote desired content
  residual: empty -> layout verified by re-probe
```

### `parallel_fixpoint.py`

Provisions a service dependency graph with the `Parallel` executor, so each dependency wave fans across a thread pool, and settles a multi-pass replica scale-up with `Fixpoint` convergence.

```
Resolved waves (Kahn):
    wave 0: Network
    wave 1: Cache, Database
    wave 2: AppServers

Converge (Parallel executor, Fixpoint convergence):
  applied this run: 6 change(s)
    + Network: provisioned
    + Cache: provisioned
    + Database: provisioned
    + AppServers: scaled up to 1 replica(s)
    + AppServers: scaled up to 2 replica(s)
    + AppServers: scaled up to 3 replica(s)
  residual: empty -> whole stack verified by re-probe
```

---

## Install

```
pip install state-reconciler
```

Nothing else is pulled in. The kernel leans on the standard library alone, and everything you need is re-exported from the top-level package.

---

## When not to reach for this

The re-probe is the entire guarantee, and it is only as honest as your `drift()`. If a step cannot observe the thing it changed, `converge()` can't tell a real fix from a no-op. An empty residual then means only that `drift()` returned nothing. Write `drift()` to read the world, never to echo what `apply()` intended.

A few more boundaries, stated plainly:

- Reconciliation earns its keep when a system will drift and you want it to keep correcting itself. If all you need is a one-shot transformation that runs once and is never checked again, a plain function is simpler and you should write that instead. The value here is the loop.
- It does not watch, poll, or schedule on its own. A `Controller` advances one tick per item you feed it, and the clock is yours.
- It's not a state store. It keeps no history and no desired-state document. Each step owns its own notion of desired and observed.
- Parallel execution uses threads, not processes, so CPU-bound apply work will not scale across cores. It is built for I/O-bound reconciliation.

---

## Development

```
pip install -e ".[dev]"
ruff check .
pytest --cov=state_reconciler --cov-report=term-missing
```

The suite is written on the standard-library `unittest` framework with subtests and runs under pytest with coverage, currently at 100% across ordering, execution, convergence, cancellation, planning, and pruning.

---

## License

Released under the MIT License. See [LICENSE](LICENSE).
