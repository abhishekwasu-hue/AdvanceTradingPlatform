"""AUD-G1.3 (owner audit 28 Oct): production and staging serve no /docs, /redoc or /openapi.json."""
from app.core.config import api_docs_enabled


def test_docs_only_outside_hardened_environments():
    assert api_docs_enabled("development") and api_docs_enabled("test")
    assert not api_docs_enabled("production") and not api_docs_enabled("staging")


def test_the_app_wires_the_switch():
    import inspect

    from app import main
    src = inspect.getsource(main)
    assert 'docs_url="/docs" if api_docs_enabled() else None' in src and 'openapi_url="/openapi.json" if api_docs_enabled() else None' in src
