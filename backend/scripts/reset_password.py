#!/usr/bin/env python
"""Login fix (handover 8d): the owner sets a user's new password from the server until e-mail (SMTP) is configured.

    printf '%s\n' "$NEW_PASSWORD" | python scripts/reset_password.py user@example.com

`deploy/atp.sh reset-password <email>` is the way to run it: it asks for the password twice without echoing it and
passes it here on stdin. The password is never an argument (other users of the host could read it in the process
list), never printed and never logged. The same password rules as the app apply.

Like completing a reset link (app/auth/routes.py reset_password): the password is replaced, every session of the user
is signed out and an audit row is written. Exit codes: 0 done, 2 usage, 3 no active user with that e-mail, 4 the
password breaks a rule (the rule is printed, never the password).
"""
import asyncio
import os
import sys
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def reset(email: str, password: str, session_factory=None) -> tuple[int, str]:
    """Sets the password. Returns (exit code, one line for the owner)."""
    from sqlalchemy import select

    from app.audit.log import write_audit_log
    from app.auth.passwords import password_problem
    from app.auth.security import hash_password_async
    from app.auth.sessions import revoke_all_sessions
    from app.db.models import User

    if session_factory is None:
        from app.db.session import _session_factory as session_factory
    email = email.strip().lower()
    async with session_factory() as session:
        user: Optional[User] = await session.scalar(select(User).where(User.email == email))
        if user is None or not user.is_active:
            return 3, f"no active user with the e-mail {email}"
        problem = password_problem(password, email)
        if problem:
            return 4, f"password not changed: {problem}"
        user.hashed_password = await hash_password_async(password)
        signed_out = await revoke_all_sessions(session, user.id, "password reset by the owner")
        await write_audit_log(session, user.tenant_id, user.id, "password_reset_by_owner", "set on the server (atp.sh reset-password)")
        await session.commit()
    return 0, f"password changed for {email}; {signed_out} session(s) signed out"


def main(argv: list[str]) -> int:
    if len(argv) != 2 or "@" not in argv[1]:
        print("usage: reset_password.py <email>   (the new password on stdin)", file=sys.stderr)
        return 2
    password = sys.stdin.readline().rstrip("\r\n")
    if not password:
        print("password not changed: no password on stdin", file=sys.stderr)
        return 4
    code, line = asyncio.run(reset(argv[1], password))
    print(line, file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
