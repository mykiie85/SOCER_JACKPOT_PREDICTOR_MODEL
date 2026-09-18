"""stdlib stand-in for pytest: import each tests/test_*.py, call every test_*."""
import importlib, pathlib, sys, traceback
sys.path.insert(0, ".")
ok = fail = 0
for path in sorted(pathlib.Path("tests").glob("test_*.py")):
    mod = importlib.import_module(f"tests.{path.stem}")
    for name in dir(mod):
        if name.startswith("test_") and callable(getattr(mod, name)):
            try:
                getattr(mod, name)(); ok += 1
            except Exception:
                fail += 1; print(f"FAIL {path.stem}.{name}"); traceback.print_exc()
print(f"{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
