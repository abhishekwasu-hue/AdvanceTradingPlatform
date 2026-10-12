"""Z10-f: the support bot - how-to answers about using the platform, nothing else.

* `knowledge` - the corpus (the help pages in `content/help.md` and the sections of the legal pages) and a small
                keyword retriever with a confidence score.
* `guard`     - turns away what the bot must not answer: advice / guarantee questions, questions about the user's own
                positions, orders or P&L (it has no tool that reads them) and requests to place, change or exit orders
                (it has no tool that touches them).
* `bot`       - one reply: refused, answered from the help (optionally reworded by the organisation's own AI provider,
                screened by the advice/guarantee filter), or handed off to the feedback form when confidence is low.
* `routes`    - `POST /api/ai/support/ask`, behind the flag `support_bot` (OFF by default: 404), rate-limited.
"""
FLAG = "support_bot"
