"""A3 (review R3, RDY-9): one audit-trail row for a deploy that skipped the CI gate.

    printf '%s\\n' "$REASON" | python scripts/deploy_audit.py deploy_untested <commit sha>

Run by `atp.sh deploy` inside the new release's backend container after it is healthy, only when the owner deployed
with ATP_ALLOW_UNTESTED=1. The reason comes on stdin (one line, at most 200 characters; control characters dropped),
never as an argument. The row has no organisation and no user (a host action), event `deploy_untested`, detail
"<sha 12> - <reason>", and goes through `write_audit_log`, so the hash chain stays whole. Exit 0 written, 2 usage.
"""
import asyncio
import re
import sys

EVENTS = {"deploy_untested"}


def clean_reason(text: str) -> str:
    return re.sub(r"[\x00-\x1f\x7f]", "", text).strip()[:200]


async def record(event: str, sha: str, reason: str, session_factory=None) -> str:
    from app.audit.log import write_audit_log

    if session_factory is None:
        from app.db.session import _session_factory as session_factory
    detail = f"{sha[:12]} - {reason}"
    async with session_factory() as session:
        await write_audit_log(session, None, None, event, detail)
        await session.commit()
    return detail


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] not in EVENTS or not re.fullmatch(r"[0-9a-f]{40}", argv[2]):
        print("usage: deploy_audit.py deploy_untested <40-character commit sha>   (the reason on stdin)", file=sys.stderr)
        return 2
    reason = clean_reason(sys.stdin.readline())
    if not reason:
        print("no reason on stdin - nothing written", file=sys.stderr)
        return 2
    detail = asyncio.run(record(argv[1], argv[2], reason))
    print(f"audit: {argv[1]} {detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
