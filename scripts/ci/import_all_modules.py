"""Import every module of the package, so code that fails only when it runs fails in CI.

``compileall`` checks syntax only. A module can compile on a Python and still raise when it is
imported there: a dataclass default that Python 3.11 rejects as mutable, a standard-library name
added in a later release, an annotation evaluated when its class is created. The unit tests import
only part of the package, so CI runs this on the declared Python floor (``tox -e py311``) first.

Importing ``mlflow_oidc_auth.app`` builds the app, which migrates the auth database and, with
workspaces enabled, writes to MLflow's workspace store. So the imports run in CI's configuration
whatever the caller's: no ``.env``, no external configuration provider, workspaces off, and a
throwaway auth database.

Usage: ``python scripts/ci/import_all_modules.py``
"""

import importlib
import os
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Dict, List

import dotenv

PACKAGE = Path(__file__).resolve().parents[2] / "mlflow_oidc_auth"

#: Modules that cannot be imported on their own: Alembic's environment runs a migration when imported.
SKIP = {"mlflow_oidc_auth.db.migrations.env"}


def module_names(package: Path) -> List[str]:
    """Name every module of ``package``, tests excluded.

    Parameters:
        package: The package directory.

    Returns:
        Dotted module names, sorted, without those in ``SKIP``.
    """
    names = set()
    for path in package.rglob("*.py"):
        parts = path.relative_to(package.parent).with_suffix("").parts
        if "tests" in parts:
            continue
        if parts[-1] == "__init__":
            parts = parts[:-1]
        names.add(".".join(parts))
    return sorted(names - SKIP)


def isolate(workdir: Path) -> None:
    """Give the imports CI's configuration, with the auth database in ``workdir``.

    Must run before anything imports ``mlflow_oidc_auth.config``, which loads ``.env`` when imported.

    Parameters:
        workdir: A throwaway directory.
    """
    # As tests/conftest.py does: config binds load_dotenv when imported, so it gets the no-op.
    dotenv.load_dotenv = lambda *args, **kwargs: False
    # The Vault, AWS, Azure and Kubernetes configuration providers turn on with CONFIG_*_ENABLED.
    for name in [name for name in os.environ if name.startswith("CONFIG_") and name.endswith("_ENABLED")]:
        del os.environ[name]
    os.environ["MLFLOW_ENABLE_WORKSPACES"] = "false"
    os.environ["OIDC_USERS_DB_URI"] = f"sqlite:///{workdir / 'auth.db'}"


def import_all(names: List[str]) -> Dict[str, str]:
    """Import each module, keeping the traceback of every one that raises.

    Parameters:
        names: Dotted module names.

    Returns:
        The failing modules mapped to their tracebacks; empty when all imported.
    """
    failures = {}
    for name in names:
        try:
            importlib.import_module(name)
        except (Exception, SystemExit):  # any failure counts, not only ImportError
            failures[name] = traceback.format_exc().strip()
    return failures


def main() -> int:
    """Import the whole package from this checkout and print a summary.

    Returns:
        Process exit code: 0 when every module imported, 1 otherwise.
    """
    names = module_names(PACKAGE)
    if not names:
        # A wrong PACKAGE would otherwise pass, having checked nothing.
        print(f"ERROR: no modules found under {PACKAGE}", file=sys.stderr)
        return 1
    sys.path.insert(0, str(PACKAGE.parent))
    # The auth database is still open when the directory goes; Windows cannot delete an open file.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as workdir:
        isolate(Path(workdir))
        failures = import_all(names)
    python = sys.version.split()[0]
    if not failures:
        print(f"OK: imported all {len(names)} modules on Python {python}")
        return 0
    # A module that fails takes every module importing it down too: show the first traceback in
    # full, then one line per module.
    print(f"ERROR: {len(failures)} of {len(names)} modules failed to import on Python {python}", file=sys.stderr)
    print(next(iter(failures.values())), file=sys.stderr)
    for name, error in failures.items():
        print(f"ERROR: {name}: {error.splitlines()[-1]}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
