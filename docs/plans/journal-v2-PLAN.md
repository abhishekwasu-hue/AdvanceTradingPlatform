# Journal v2 (P2-8) — PLAN

Order: docs/owner-orders/ATP_SESSION_JOURNAL.md. Default carried out: OPEN_QUESTIONS NEXT-Q1 (21 Nov "pudhil kaam kr").
Owner questions with defaults: OPEN_QUESTIONS "JV2" section. Three draft PRs, no merge before the owner's ✅.
Extends `backend/app/journal`, `frontend/src/journal/JournalPageV2.tsx` and the `discipline_engine` flag; rewrites nothing.

## 1. Audit (21 Nov, grep + line ranges) — confirms docs/design/ROADMAP_TRUTH_21NOV.md

| Part | On main (file:line) | Gap |
|---|---|---|
| J1-1 | file import `journal/importer.py` (`pair_fifo` :557, duplicate key :465, commit :810); read-only broker lists `me/broker_book.py` (`account_book` :151, positions / holdings / orders only); `get_trade_book` on every adapter (`brokers/upstox.py:571`: today's fills only) | nothing reads the broker trade book into the journal; no matching to ATP orders |
| J1-2 | `entries.chart_png` :572 renders on every request ("never stored", :505); `trade_chart.py` for manual trades | no stored snapshot, nothing to erase |
| J1-3 | `service.review_trades` :327 and `stats._decisions` :277 sum legs per `leg_group_id` for stats / review | the list and the entry sheet show one row per LEG; no credit, no planned vs actual max loss |
| J1-4 | `capture.plan_of` :145 (signal price, initial stop, target1/2), stop history, exit reason / filled quantity on the trade | no line comparing plan and outcome |
| J2 | `checklist_json`, `user_tags_json`, notes (models.py:483) | no feeling / confidence / plan-followed / mistake fields |
| J3 | weekly review `journal/review.py` (`build_review` :167, `advice_in` :91), `WeeklyReviewV2.tsx` | no daily card, no monthly page, no feeling / confidence vs result, no first-20 rule |
| J4 / J5 | none | all |
| J6 | Journal tabs `JournalPageV2.tsx:31` (trades, stats, review, studies, settings); Telegram per user `rhythm/deliver.py` (`destination` :58, opt-in, default OFF); e-mail `notifications/mailer.py:30` | no journal delivery, no Today / My proof / Plans tabs, no tour |
| Privacy | export `me/account.py:68` (journal_entries, journal_tags); erase `retention/service.py:294`; admin console reads only `captured_at` (`admin/console.py:245`) | new tables must join export + erase; feelings never in logs / admin / analytics exports (`exports/service.py:92` has no journal dataset) |
| Safety | capture hooks on their own queue / connection (`capture.py:329-407`), `tests/test_z6_capture_concurrency.py` | keep: no new hook work on the order path |

## 2. PR 1 — J1 gaps (`claude/journal-v2-j1`, migration YES, additive, ROLLBACK_SAFE = True)

- J1-1 `journal/broker_sync.py`: reads `get_trade_book` of each of his own signed-in broker sessions
  (`entitlement.own_records`, the same rate budget as broker_book). Pure `plan_sync(fills, own_order_ids, seen, open_entries)`:
  a fill of an ATP order (his trade's `broker_order_id` / `exit_order_id`) = MATCHED (no second entry); a fill already
  stored = skipped; the rest close his open broker entries first (FIFO, a partial close splits the entry), then pair
  FIFO into new outside-ATP entries with `origin = BROKER` ("From your broker"). New table `journal_broker_fills`
  (unique user + broker + fill id) = no duplicates on any re-run. Runs on "Sync from broker" (Journal) and once a day
  from the worker after the close (JV2-Q1); read-only, never an order call.
- J1-2 `journal/snapshots.py`: the entry / exit PNG of an ATP trade is stored on first draw (private store, per user),
  served from the store afterwards; the bars end at the instant (no look-ahead). Table `journal_snapshots`; erased with
  the account; rows in the account export.
- J1-3 `journal/spread.py`: pure `spread_summary(legs, meta)` = combined P&L, credit (or debit) received at entry,
  planned max loss (the structure's own `group_meta`) vs actual loss. The list folds the legs of one `leg_group_id`
  into ONE row; the entry sheet shows the summary and every leg.
- J1-4 `journal/plan_actual.py`: pure `plan_vs_actual(...)` -> lines only where plan and outcome differ (fill vs signal
  price, stop moved / removed, exit by hand before his own levels, partial exit fill). Every line passes `advice_in`.

## 3. PR 2 — J2 check-in + J3 reviews (`claude/journal-v2-j2j3`, migration YES)

- Table `journal_checkins` (one row per entry ref: feeling before / after, confidence 1-5, plan followed Y / P / N,
  mistake category, one free line, setup name, notes during as a JSON list). His words only; nothing inferred, no
  score. The auto tags sit beside his answer; a detector re-run never writes this table.
- Privacy: only the owner's routes read it; never logged (no route logs the body); not in admin pages / analytics
  exports (test: an admin of the same organisation gets 404); in the account export; erased with the account.
- Prompt at OPEN and CLOSE: an in-app card on the Journal (no blocking, no hook on the order path). Telegram / push
  delivery joins J6 (PR 3).
- `journal/reviews.py` (pure): daily card, weekly extras, monthly summary; `min_closed_for_review` setting (default 20):
  fewer closed trades -> "Not enough trades yet (N of 20)" and no percentages; small groups labelled. One "done well"
  line chosen from facts. Weekly reflection answer stored. Every line through `advice_in`.

## 4. PR 3 — J4 + J5 + J6 (`claude/journal-v2-j4j6`, migration YES)

- `journal/proof.py` (pure): facts of good process with date + trade link; his own entries, pin. Today card + top of
  the Journal + after a loss ("In your last 20 trades you followed your plan in N").
- If-then plans + process goals: his own text; pre-market card; optional reminder after a stop hit / loss; never blocks.
- Delivery: daily / weekly / monthly on Telegram and e-mail, default OFF, his channels and times (rhythm pattern).
- Tabs Today · Trades · Reviews · My proof · Plans (+ the existing Stats / Studies / Settings under them) and a page tour.

## 5. Rules kept in every PR

Observations only; no buy / sell / should / target wording; feelings = only what he typed; no change to order paths,
exits or risk limits; the same code PAPER and LIVE; new UI behind `ui_v2` + `discipline_engine`; English only; tests
for every pure function, privacy, the advice guard, empty states and the first-20 rule; one comparison page per PR.
