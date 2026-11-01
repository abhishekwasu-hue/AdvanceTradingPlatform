# ATP ORDER M83 - addendum to M82: one "Read now" for Market pulse (owner order, 1 Nov, verbatim)

M83 (ATP) - addendum to M82 (owner test on phone, market closed, broker connected): pressing "Read now" in Market memory filled only Global cues; Market memory still says "The memory is empty" and the Today's read / Sentiment cards above did not change. No error was shown.
A1 Root cause first: why the broker read stored nothing (token? market closed? endpoint error?) - one line in the report. A failed read must show its exact reason on the panel, never a silent "empty".
A2 ONE "Read now" at the top of Market pulse (sticky header of the tab), refreshing every card on the tab together (Today's read, Sentiment, Market memory, Global cues and the rest); each card shows its own "updated <time>" or its own failure reason. Remove the per-card duplicates or make them call the same action.
A3 After the read, every card re-renders from the new data without a page reload (shared query invalidation / one store).
Proof: phone 390 + desktop before/after of the tab after one press. Report in Marathi <= 25 lines.
