# anyio 4.13 is incompatible with pytest 9.x's assertion rewriter.
# anyio ships a pytest11 entry-point, so pytest marks ALL its submodules for
# rewriting.  When it then rewrites them the output is broken bytecode that
# causes ImportError at collection time.
#
# Fix: remove anyio (and fastapi/starlette, which embed anyio) from the
# AssertionRewritingHook._must_rewrite set right after plugin discovery has
# populated it but before test collection imports those packages.

from __future__ import annotations


def pytest_configure(config) -> None:  # noqa: ANN001
    try:
        from _pytest.assertion.rewrite import AssertionRewritingHook

        for plugin in config.pluginmanager.get_plugins():
            if isinstance(plugin, AssertionRewritingHook):
                for pkg in ("anyio", "fastapi", "starlette"):
                    plugin._must_rewrite.discard(pkg)
                plugin._marked_for_rewrite_cache.clear()
                break
    except Exception:  # noqa: BLE001
        pass
