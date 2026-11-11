# M95 F7 - what is left before `marketing_site` goes ON

The flag stays OFF (owner switch, "public launch only"). M95 PR-1 gave the three pages the front door's content
(`backend/app/learn/site.json`), picture boxes, the FAQ and the M94 footer. Still open, each with a default:

| # | Left | Why | Default |
|---|---|---|---|
| 1 | The six pictures `frontend/public/front/<slug>.png`: `copilot`, `option-chain`, `find-setups`, `trading-bot`, `journal`, `daily-report` | Not in the repo; the boxes show a plain placeholder. The front door (PR-2) reads the same files. | Made like `public/og/front-door.png` (`frontend/scripts/og-image/`): app screens, index data only, at least three months old, every price hidden (M95-Q1). 640 x 400 or larger at 16:10. |
| 2 | Per-page tags for link previews and search | `index.html` tags are the same on every address; `applySeo` runs only in the browser. A shared `/features` link shows the front-door card. | A build step that writes `dist/<slug>/index.html` with the page's own title, description and canonical. nginx already tries `$uri/` first, so no server change. |
| 3 | Sitemap and robots while OFF | `public/sitemap.xml` and `robots.txt` list `/features`, `/how-it-works` and `/about` today. While OFF those addresses answer 200 with "This page does not exist." (a soft 404). | Leave as is until ON (the pages are not linked anywhere while OFF), or drop the three lines until the switch. |
| 4 | Unknown address status | Every unknown path is `index.html` with 200 (SPA fallback). | Fine for the launch; a real 404 needs the prerender of item 2. |
| 5 | Legal | Terms, Privacy and Risk disclosure are linked in the footer. CLAUDE.md §3: legal advice before LIVE. No contact / grievance page. | A legal read of the three pages and the FAQ before ON; a Contact link if the lawyer asks for one. |
| 6 | "Continue with Google" (step 1) | True only when the server has the Google keys (M95-Q2, not known from the code). | Turn ON only once Google sign-in works on production; otherwise change step 1 to "Sign in with your email". |
| 7 | FAQ cost | "Free during the beta." is written in `site.json`, not read from the plans route. | Change the line when Abhi sets a price (M95-Q6). |
| 8 | FAQ brokers | The list is written by hand. Kotak Neo and CoinDCX are behind "and more": "Kotak" is a security name the content guard blocks. | Keep in step with `backend/app/brokers/registry.py` when a broker is added. |
| 9 | Marathi / Hindi | The site pages are English only; F5's switch covers the front door only. | Later, with Abhi's check of the Marathi (M95-Q4 rule). |
| 10 | Phone and speed check on the real site | The e2e baselines cover 360 / 390 / 1440; the pictures are lazy with fixed boxes. | One Lighthouse run on production after the pictures are in (`frontend/lighthouserc.json`). |
