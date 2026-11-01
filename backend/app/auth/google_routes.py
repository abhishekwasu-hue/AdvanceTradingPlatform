"""GS-1: the Google sign-in routes - since M73 served by the provider-generic layer (app/auth/social_routes.py), which
keeps every GS-1 address and behaviour: /api/auth/providers, /api/auth/google/start and /api/auth/google/callback (the
redirect URI registered at Google), the same flow cookie (`atp_google_flow`, path /api/auth/google), the same refresh
cookie, two-factor step, lockout and audit rows. What M73 changed on purpose (owner order M73 SL-2/SL-3): a Google
e-mail that matches an account which has not linked Google is a pending link confirmed with the account's password
(never a silent sign-in), and a sign-up that needs the terms or a beta code finishes on a short step instead of being
sent back to the form. This module stays as the import point main.py and older code use.
"""
from app.auth.social_routes import router, social_start_rate_limit

google_rate_limit = social_start_rate_limit

NO_ACCOUNT_MESSAGE = "No account uses this Google e-mail. Ask your organisation owner for an invite."

__all__ = ["router", "google_rate_limit", "NO_ACCOUNT_MESSAGE"]
