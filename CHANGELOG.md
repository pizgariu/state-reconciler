# Changelog

All notable changes to state-reconciler are recorded here. This project follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and [Semantic Versioning](https://semver.org/spec/v2.0.0.html). What reconciliation is, and why this kernel exists, lives in the [README](README.md).

Every release is a pre-release on the road to the 1.0.0 freeze.

## [Unreleased]

Nothing yet.

## [0.1.6] - 2026-07-21

### Added
- **Changelog.** This file, following Keep a Changelog, with a section per release and a roadmap of what is planned next.

## [0.1.5] - 2026-07-21

### Added
- **Continuous integration.** A GitHub Actions pipeline that lints with Ruff and runs the suite on CPython 3.10 through 3.14, on every push to `master` and every pull request, with `fail-fast` off so a break on one interpreter does not hide the others. Dependabot keeps the pip and Actions dependencies current.

## [0.1.4] - 2026-07-20

### Added
- **Test suite.** Broad coverage of the kernel across the ordering strategies, executors, convergence policies and cancellation sources, at 100 percent line and branch coverage, split by concern.

## [0.1.3] - 2026-07-18

### Added
- **README.** The full documentation: the reconciliation principle, the building blocks, the pluggable behaviour axes, and the runnable examples walked through end to end.

## [0.1.2] - 2026-07-18

### Added
- **Examples.** Three runnable, self-contained scripts. `filesystem_layout.py` brings a temp directory tree to a desired layout, then tampers with a file and self-heals on the next converge. `git_repo_state.py` drives a throwaway git repository to desired state and proves success with an empty residual. `parallel_fixpoint.py` provisions a service dependency graph with the `Parallel` executor and settles a multi-pass replica scale-up under `Fixpoint` convergence.

## [0.1.1] - 2026-07-15

### Added
- **Packaging.** Packaged for PyPI with hatchling and released under the MIT License, with a `py.typed` marker so downstream type checkers see the annotations.

## [0.1.0] - 2026-07-13

### Added

- **Reconciliation kernel.** A domain-agnostic engine with zero dependencies. Steps own the desired state. A Reconciler resolves their order, converges actual toward desired, then self-verifies by re-probing for any drift that survived. The kernel carries no domain vocabulary. It reads a drift item only through the two fields of the `Drift` protocol, its `name` and its `message`.
- **Drift surface.** The `Drift` protocol as the single interface the kernel reads drift through, and `DriftItem` as the concrete carrier of a name and a message.
- **Step surface.** `Step`, the unit of desired state, with an `after` class attribute that declares dependencies on other step types. A step reports through `drift()`, `plan()`, `audit()`, `footprint()` and `prune()`, and closes gaps through `apply()`.
- **Reconciler and Controller.** `Reconciler` wires steps to an ordering, an executor, a convergence policy, and a cancellation source. It exposes `drift()`, `plan()`, `audit()`, `footprint()`, `prune()` and `converge()`. `converge()` returns a `Residual`, the drift that outlived the pass, with the applied changes carried on a separate channel. An empty residual means the state was verified clean. `Controller` drives one convergence per tick, with `run()` for a lazy stream of residuals and `settle()` to stop on the first clean pass.
- **Ordering strategies.** Run order is resolved from the `after` dependency graph. `Kahn` is the default and sorts into dependency waves. For a flat post-order, use `DFS`. `Priority` walks a best-first frontier over a caller-supplied key. `Components` splits the graph into independent chains.
- **Executors.** `Serial` runs one step at a time and is the default. `Parallel` fans each dependency wave across a thread pool. `Pipeline` runs independent chains concurrently. Both concurrent executors are context managers that release their pool on exit. An `OnError` policy chooses between `FailFast` and `BestEffort`.
- **Convergence policies.** `Once` runs a single apply-then-probe pass and is the default. `Fixpoint` repeats the loop until the residual stops changing or a pass ceiling is reached.
- **Cancellation.** Cooperative abort between steps through the `Cancellation` base, a wall-clock `Deadline`, a manual `Flag`, and the composites `AnyOf`, `AllOf` and `Majority` that nest into a tree, or a `Quorum` under a custom `Some`, `Every` or `Most` rule. `Cancelled` is raised when a run is aborted.

## Roadmap

Planned milestones, in rough order. Nothing here is a promise of scope.

- **1.0.0** - API stability. Freeze the public surface and commit to Semantic Versioning guarantees for it. The first non-prerelease, cut from 0.11.0.
- **1.1.0** - Public testing utilities. Ship the reusable doubles the test suite grew - a `Staged` step, a `Fixable` step, a `RecordingBackoff`, a recording `Observer` - as a supported `state_reconciler.testing` module, so a domain tests its own Steps and strategies against ready-made fakes. A backwards-compatible new surface, a minor after the freeze.
- **2.0.0** - Capability-based dependencies, a fourth edge kind. A step would declare what it PROVIDES (a capability, not a concrete class) and depend on capabilities rather than named types, the order resolved by matching what each step supports against what the others require - the way systemd `Provides=` or a Debian virtual package does. Threaded through the one shared edge derivation so `verify()`, the `Ledger`'s blocking and `Only`'s closure all honour it, which is why it belongs in a major version after the freeze.

[unreleased]: https://github.com/pizgariu/state-reconciler/compare/v0.1.6...HEAD
[0.1.6]: https://github.com/pizgariu/state-reconciler/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/pizgariu/state-reconciler/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/pizgariu/state-reconciler/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/pizgariu/state-reconciler/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/pizgariu/state-reconciler/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/pizgariu/state-reconciler/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/pizgariu/state-reconciler/releases/tag/v0.1.0
