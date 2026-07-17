"""Bring a directory tree to a desired layout: the right directories, the right files, the right content.

Two Step kinds model an on-disk scaffold:

  Directory  ensure a directory exists (created with its parents)
  TextFile   ensure a file exists and holds exactly the desired content

TextFile declares `after = (Directory,)`. Because after edges are keyed by CLASS, that one
line means every directory is created before any file is written, no matter what order the
steps are supplied in. The files deliberately do NOT create their own parent directories, so
the dependency is load-bearing: without it, a file could be written before its directory
exists.

drift() reads the filesystem (missing directory, missing file, or wrong content) and apply()
writes it. converge() re-probes afterward, so an empty residual is a checked fact.

The example also shows self-healing: after the tree converges, it tampers with one file on
disk, then converges again. drift() catches the changed content and apply() rewrites it.

Run it:

    python examples/filesystem_layout.py

Expected stdout: an initial plan listing the missing directories and files, a first converge
that creates them and returns an empty residual, a clean second converge, then a tamper step
that reintroduces exactly one drift which the next converge repairs. The whole tree is built
under a temp directory and removed at the end.
"""

import shutil
import sys
import tempfile
from pathlib import Path

try:
    from state_reconciler import DriftItem, Reconciler, Step
except ModuleNotFoundError:  # running from a source checkout without an install
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from state_reconciler import DriftItem, Reconciler, Step


class Directory(Step):
    """Ensure a directory (and any missing parents) exists."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def drift(self) -> list[DriftItem]:
        if self.path.is_dir():
            return []
        return [DriftItem(str(self.path), "directory is missing")]

    def apply(self) -> list[DriftItem] | None:
        if self.path.is_dir():
            return None
        self.path.mkdir(parents=True, exist_ok=True)
        return [DriftItem(str(self.path), "created directory")]


class TextFile(Step):
    """Ensure a text file exists and holds exactly the desired content."""

    after = (Directory,)

    def __init__(self, path: Path, content: str):
        self.path = Path(path)
        self.content = content

    def drift(self) -> list[DriftItem]:
        if not self.path.is_file():
            return [DriftItem(str(self.path), "file is missing")]
        if self.path.read_text(encoding="utf-8") != self.content:
            return [DriftItem(str(self.path), "content does not match desired")]
        return []

    def apply(self) -> list[DriftItem] | None:
        if not self.drift():
            return None
        # No parents=True on purpose: the Directory step must have run first.
        self.path.write_text(self.content, encoding="utf-8")
        return [DriftItem(str(self.path), "wrote desired content")]


def report(residual) -> None:
    if residual.applied:
        print("  applied this run:")
        for item in residual.applied:
            print(f"    + {item}")
    else:
        print("  applied this run: nothing")
    if residual:
        print("  residual (still wrong):")
        for item in residual:
            print(f"    ! {item}")
    else:
        print("  residual: empty -> layout verified by re-probe")


def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="reconciler-fs-"))
    try:
        app_toml = root / "config" / "app.toml"
        # Files listed before their directories, to show the ordering resolves it.
        steps = [
            TextFile(root / "README.md", "# Demo site\n"),
            TextFile(app_toml, "name = \"demo\"\nport = 8080\n"),
            TextFile(root / "config" / "logging.ini", "[root]\nlevel = INFO\n"),
            Directory(root),
            Directory(root / "config"),
        ]
        reconciler = Reconciler(steps)

        print(f"Layout root: {root}")
        print()
        print("Plan (missing pieces, in resolved order - directories before files):")
        for item in reconciler.plan():
            print(f"    - {item}")
        print()

        print("First converge:")
        report(reconciler.converge())
        print()

        print("Second converge (should be a clean no-op):")
        report(reconciler.converge())
        print()

        print("Tampering: overwrite config/app.toml with the wrong content")
        app_toml.write_text("name = \"tampered\"\nport = 1\n", encoding="utf-8")
        drift = reconciler.drift()
        print(f"  drift now sees {len(drift)} problem(s):")
        for item in drift:
            print(f"    ! {item}")
        print()

        print("Converge again to self-heal:")
        report(reconciler.converge())
        print()

        print(f"Final config/app.toml content:\n{app_toml.read_text(encoding='utf-8')}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()