"""H-C1 f: every LLM call carries a prompt version (G4), so evals and the audit can tell prompts apart.

A version is the prompt's name plus a short hash of its template text: editing a prompt changes its version without
anyone remembering to bump a number. Callers set it on the provider before calling (`stamp`); the metering layer logs
`unversioned:<feature>` if a caller forgot - never NULL - and a test keeps that list empty.
"""
import hashlib


def version_of(name: str, *templates: str) -> str:
    digest = hashlib.sha256("\x1e".join(templates).encode("utf-8")).hexdigest()[:8]
    return f"{name}-{digest}"


def stamp(provider, version: str):
    """Set the version on a (metered) provider that takes one; providers without the attribute are left alone."""
    if hasattr(provider, "prompt_version"):
        provider.prompt_version = version
    return provider


__all__ = ["version_of", "stamp"]
