# M95 Front door + link preview - spec review against the code (10 Nov)

Owner order M95 (INBOX / DECISIONS 10 Nov): friends did not open the shared link because nothing told them what the
app is. This file is the review CLAUDE.md §6 asks for before a big spec is built: the code as it is, what each part
needs, where the order meets a standing rule (owner questions, each with a default), and the PR plan.

## 1. The code today (facts, file:line)

**Link preview (F1)**
- `frontend/index.html` is static. nginx sends every path to it (`frontend/nginx.conf:55-57`). No meta tags are rendered on the
  server, so a crawler (WhatsApp, Telegram) sees the same tags on every path.
- Today's tags: title and og:title "AiAlgoDesk · Learn, analyse and practise trading on paper". og:description "Analysis, alerts
  and paper trading for Indian markets. ..." og:image `icons/icon-512.png` (the logo). twitter:card `summary`. og:locale `en_IN`.
- `site/site.ts:59-75 applySeo()` changes the tags in the browser only; crawlers do not run JS.
- The index.html comment points to `src/site/seo.ts`, a file that does not exist (stale).

**Login page (F2)**
- Signed-out visitors at any address get `SignInScreen` (`App.tsx:234-250, 372`). It shows the logo, "AiAlgoDesk", a form and
  the footer "Information and education, not investment advice." There is no line saying what the app is.
- Google sign-in is built. Config `GOOGLE_OAUTH_CLIENT_ID / _SECRET` (`core/config.py:768-772`); both empty means no button and
  404 routes. `GET /api/auth/providers` tells the screen. Under `ui_v2` the provider buttons are already on top, then "or use
  email".
- Whether the production server has the Google keys is **not known** from the code (OWNER_RULES A.2).
- `self_signup` (status "decision", OFF for the invite-only beta). When OFF the screen says "New here? Ask your team owner for
  an invite link."; `POST /register` and provider sign-ups answer 403. There is no "ask for an invite" request today.

**Guest demo (F3)**
- No guest or demo mode exists. Every demo-relevant read needs sign-in: EOD bars and chain (`market_data/eod_routes.py:98-175`),
  daily report, Today, zones, screener runs.
- Shared, non-broker data at rest:
  - `md_candles`, the EOD lake from the bhavcopy. Option rows exist only if the F&O options file was loaded.
  - `shared_daily_reports`: platform-wide, but flag `shared_daily_report` is OFF and the symbol list is empty by default.
  - Platform-scope `market_zone_runs`: flag `market_zones` OFF, `MARKET_ZONES_SYMBOLS` empty by default.
  - The latest chain from the bhavcopy (`/eod/chain`).
- Screener runs and Today are per user. There is no stored platform "sample run".
- Rate limits are per IP only on the auth routes. A public read would need its own per-IP limit.

**Language (F5)**
- i18next exists only for the Copilot. The interview has a Marathi bundle; the Hindi slot is reserved but empty.
- `scripts/check_english_only.py` fails on any Devanagari outside an allow-list. Its rule (L11): "To add an entry, the owner's
  approval is needed (OD-2)".

**marketing_site (F7)**
- Built and OFF ("unfinished", "public launch only"):
  - `/features`, `/how-it-works`, `/about`, text in `backend/app/learn/site.json`, no images;
  - `SiteLinks` under the sign-in card (shown only when the flag is on);
  - tests in `test_c13_learning.py`, `site.test.tsx` and `learn-growth-v2.e2e.ts`.

**Video (F4)**: no video component. M92 E1 is not delivered yet.

**Footer (F6)**: "Information and education only, not investment advice." is the M94 sentence. The login and site pages say
"Information and education, not investment advice." (without "only").

## 2. Where the order meets a standing rule (owner questions)

Per the 10 Nov rule "no own decisions", each item below is settled only by the owner. The parts that depend on one wait; the
other parts are built now.

| ID | Question | Default (applied only after the owner's word) |
|---|---|---|
| M95-Q1 | **Public surface rule (CLAUDE.md §3): "public / education surfaces: no names, no prices, charts older than three months."** The front door, the link image and the guest demo are public. F1 / F2 ask for real screenshots; F3 asks for "option chain last close" and "a chart with areas" (current data, names and prices). | Real app screens from **index data only** (NIFTY 50 / BANK NIFTY, allowed as market references, OPEN_QUESTIONS LAB-14), **as of a date at least three months old**, with every price figure and price axis hidden (OI in lakhs, % and structure words stay). The guest demo is a frozen "As of <date>" snapshot under the same rule, not today's close. |
| M95-Q2 | Google keys on the server: is `GOOGLE_OAUTH_CLIENT_ID / SECRET` set in production? (not known from code) | The big "Continue with Google" button shows when the server reports Google on. Otherwise email comes first and nothing is broken. If the keys are not set: Abhi adds them (steps in the PR). |
| M95-Q3 | `self_signup` OFF: F6 says "Ask for an invite". No such request exists. | A small "Ask for an invite" form: email + optional note, stored for the admin (Admin › Users › Invite requests), per-IP limited, no email sent. **Needs a migration** (one table). |
| M95-Q4 | F5 Marathi / Hindi strings and the English-only guard (OD-2) | This order counts as the approval. One new allow-listed namespace file for the front door only (`frontend/src/i18n/frontDoor.ts`, objects FRONT_MR / FRONT_HI). I write the translations; Abhi checks the Marathi. |
| M95-Q5 | F1 "Marathi-friendly variant of the description": a page has one og:description and WhatsApp shows one | English og:description + the same sentence in Marathi after " · ", in index.html (allow-listed like Q4). |
| M95-Q6 | F2 FAQ "cost": no price is decided (billing plans exist, launch pricing is an open owner item) | "Free during the beta." stays until Abhi sets a price (the answer reads the plans route, no number written by hand). |
| M95-Q7 | F1 proof "WhatsApp / Telegram preview": the preview reads the live domain, so a real preview exists only after deploy | Before deploy: a render of the card at WhatsApp's and Telegram's sizes from the real tags. After deploy: Abhi shares the link once and sends the screenshot (the real proof). |

## 3. Parts and PRs

| PR | Part | Waits on | Migration |
|---|---|---|---|
| PR-1 | F1 tags: og:title / og:description in plain words, `twitter:card summary_large_image`, og:image to a 1200x630 file (`/og/front-door.png`), og:image:width/height/alt; fix the stale seo.ts comment | image content: Q1; MR line: Q5 | no |
| PR-2 | F2 front door inside `SignInScreen` (same URL): one line, 6 benefit cards with an image slot each, Start in 3 steps, large Google button on top (when on), email below, trust lines, FAQ in Details, M94 footer sentence; phone-first 390, fast first paint (images lazy, sizes fixed) | screenshots: Q1; Google: Q2; cost: Q6 | no |
| PR-3 | F6 `self_signup` OFF: "Ask for an invite" | Q3 | yes (one table) |
| PR-4 | F5 MR / HI / EN switch on the front door (UI strings only, stored in localStorage, same content) | Q4 | no |
| PR-5 | F3 guest demo: public read-only routes per IP limited, stored outputs only, no broker data, no AI calls, sign-up bar | Q1 (data rule) | no |
| PR-6 | F4 video slot: hidden until a video URL is set (operator setting), then shown on the front door | M92 E1 | no |
| PR-7 | F7 site pages: the same content (benefits, steps, FAQ) in site.json + images; linked under the login card; a list of what is left before the flag goes ON | Q1 for images | no |

Built now without waiting:
- PR-2's structure and text (image slots empty);
- PR-1's text tags (English);
- PR-6;
- PR-7's text.

Every PR includes tests, before / after screenshots at 390 + desktop in light + dark, and the M95 proof page at the end.

## 4. Rules for every part (F6 + standing rules)
- No profit, accuracy or "AI picks" claims; no testimonials; no buy / sell / target / stop wording (the banned-words lint).
- Footer: "Information and education only, not investment advice." on the front door, the site pages and the guest demo.
- Trust lines say only what the code does:
  - "No order is placed without you": AI never places orders (ADR-0006); bots run on paper by default.
  - "Your broker data stays yours": broker-sourced data is per tenant (`md_tenant_*`, the private-source refusal in the lake).
  - "Paper by default": phase0_paper_only.
- Works with `self_signup` ON or OFF, `ui_v2` ON or OFF, and Google on or off.
