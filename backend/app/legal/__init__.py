"""Z10-f (Phase 0 launch hardening): the legal pages and their acceptance.

* `documents`  - the Privacy notice (DPDP Act 2023), Terms of use and Risk disclosure as versioned Markdown content
                 (`content/*.md`), each headed "Draft - to be reviewed by a lawyer before launch".
* `acceptance` - who accepted which version and when (at sign-up, or when asked on the next sign-in).
* `routes`     - `/api/legal/*`: the documents are public; acceptance needs a signed-in user.

Nothing here gates trading: an unaccepted document never blocks an exit, a position or any other route.
"""
