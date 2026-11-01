#!/usr/bin/env python3
"""Public repository guard: nothing private enters the tree of this public repository (docs/PUBLIC_REPO_POLICY.md).

The repository is public, so everything committed is public. This check reads every tracked and new (not ignored) file
and fails on:

  - the production server's IP address, matched by its sha256 in dotted form and with - _ / : or a space between the
    numbers ("ip-a-b-c-d"), so the literal is not written here (a sha256 of an IPv4 address is reversible in seconds:
    this only keeps the literal out of the tree, it hides nothing);
  - any other public IPv4 literal, outside the documentation ranges (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24)
    and the explicit ALLOWED_IPS below (well-known addresses the tests use); private, loopback and other non-global
    addresses are not flagged, nor is a version after a known browser token ("Chrome/120.0.0.0", "Safari/...");
  - an e-mail address, except on example / test domains (example.com, *.example, *.test, *.invalid, localhost) and the
    explicit ALLOWED_EMAILS below (public vendor support addresses, placeholders already in tests and docs);
  - a Telegram bot token, or a chat-id-like literal (a chat id / Telegram id of 7 or more digits, a -100... supergroup
    id);
  - a private key header (PEM / OpenSSH / PGP);
  - a secret file type: *.pem, *.key, *.ppk, *.jks, *.keystore, *.p12, *.pfx, *.pepk, keystore.properties,
    signing.properties, google-services.json, GoogleService-Info.plist, *service-account*.json, *credentials*.json;
    an .env file holding values (.env.example / .sample / .template are allowed); an .npmrc / .pypirc holding a token
    or password (a ${VARIABLE} reference is allowed);
  - the KB / Method Pack / Chart Reader method, by path: docs/knowledge/, *method_pack*, KNOWLEDGE_BASE*,
    *METHOD_NOTES*, CHART_READER_METHOD* (any case, "Method Pack" too; owner decision PR-1: the KB, the Method Pack and
    the reader live in the private package). By name only: a renamed copy is caught only in review;
  - a private fixture: any path listed in backend/tests/private_fixtures_manifest.json (the Trade-derived goldens moved
    to the private fixtures store, owner decision PR-2), whatever the other lists say, and any file whose raw sha256 is
    one the manifest lists (a renamed copy); a manifest that is missing (while backend/tests/private_fixtures.py is in
    the tree), unreadable or lists an absolute or '..' path is a finding too;
  - a golden / parity fixture or a real-data file (a data file whose path says golden or parity; a CSV, spreadsheet,
    compressed or image file under a fixtures folder; a trade journal, tradebook, contract note or chart export), and
    any data or compressed file under backend/tests/, backend/evals/, frontend/test/, frontend/e2e/ or a __fixtures__
    folder, and any tabular or binary data file (csv, tsv, xlsx, parquet, feather, pkl, npy, npz, h5, arrow) anywhere,
    that is not listed. ALLOWED_TEST_DATA (synthetic or hand-made data) pins each file's sha256, so new content in a
    listed file fails too; ALLOWED_PUBLIC_DATA allows public data rebuilt by automation by path. A synthetic file is
    pinned with the line `--hash` prints (the finding prints it as well). Trade's own outputs are never listed: they
    live in the private fixtures store (backend/tests/private_fixtures.py, docs/PUBLIC_REPO_POLICY.md section 5).

Every list here is explicit and narrow; adding an entry is a reviewed change. Findings are printed without the
secret-like value (redacted), because the CI log of a public repository is public too.

    python3 scripts/check_public_repo.py            # from anywhere; exit 0 = clean, 1 = findings
    python3 scripts/check_public_repo.py --root DIR # check another tree (the tests use this)
    python3 scripts/check_public_repo.py --hash PATH [PATH ...]   # the ALLOWED_TEST_DATA line that pins each file

Files: the tracked and the new (not ignored) files when the root is a git work tree, else every file under the root.
Known binary types are checked by path only; a file over 8 MB is checked by path only, with a notice.

Out of scope (a person reviews these; docs/PUBLIC_REPO_POLICY.md section 4): an IP address written as an integer or
in hex, or split across lines; values inside binary files (images, archives, PDFs), UTF-16 text or files over 8 MB;
the content of screenshots under docs/.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Dict, FrozenSet, Iterable, List, Optional, Tuple

# The production server's IPv4 address, as the sha256 of its dotted-quad text. Not a secret (the address is in the
# history), only a way to keep the literal out of the tree. It is refused wherever it appears, even where another rule
# would let an address pass.
PRODUCTION_IP_SHA256: FrozenSet[str] = frozenset({
    "293e736c1025fa34d61e5e793268df1f745b8277f10530679e50130405c1fb10",
})

# RFC 5737 documentation ranges: never a real host.
DOC_NETWORKS = tuple(ipaddress.ip_network(n) for n in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24"))

# Well-known public addresses the tests and the reference code use as examples (public resolvers, the old
# example.com address, a multicast address, obvious placeholders). Explicit; never a server of ours.
ALLOWED_IPS: Dict[str, str] = {
    "1.1.1.1": "Cloudflare public DNS (a test / reference-code example)",
    "8.8.8.8": "Google public DNS (egress / static-IP tests)",
    "9.9.9.9": "Quad9 public DNS (egress tests)",
    "1.2.3.4": "placeholder address (egress tests)",
    "5.6.7.8": "placeholder address (egress tests)",
    "93.184.216.34": "the historical example.com address (egress / injection tests)",
    "224.0.0.1": "the all-hosts multicast address (egress tests)",
}

# E-mail domains that are never a real mailbox (RFC 2606 / 6761), and the explicit addresses allowed anywhere.
_EXAMPLE_DOMAIN = re.compile(r"(?:^|\.)(?:example\.(?:com|org|net|in|invalid)|example|test|invalid|localhost)$")
ALLOWED_EMAILS: Dict[str, str] = {
    "apihelp@dhan.co": "Dhan's public API support address (docs/specs/ATP_PARTNER_EMAIL_DRAFTS.md)",
    "git@github.com": "GitHub's SSH user in the git remote of the deploy scripts, not a mailbox",
    "user@api.twilio.com": "the user-info part of a URL in an injection test (backend/tests/test_alert_gateway_injection.py)",
    "a@b.com": "a placeholder address in tests",
    "c@d.com": "a placeholder address in tests",
    "x@y.com": "a placeholder address in tests",
    "colleague@company.com": "a placeholder in the team page's invite field (frontend/src/pages/TeamPage.tsx)",
    "demo@atpdemo.in": "the UI audit's demo login (docs/design/UI_AUDIT_2026-10.md)",
    "fresh@atpdemo.in": "the UI audit's empty-account login (docs/design/UI_AUDIT_2026-10.md)",
}

# Generated files whose third-party metadata names people's contact addresses (npm deprecation notices): the e-mail rule
# only skips these; every other rule still applies.
EMAIL_EXEMPT_FILES: FrozenSet[str] = frozenset({"frontend/package-lock.json"})

# The Trade-derived goldens moved to the private fixtures store (owner decision PR-2): their manifest (paths and sha256
# only) lists them, and none of those paths may come back into the tree.
PRIVATE_FIXTURES_MANIFEST = "backend/tests/private_fixtures_manifest.json"
PRIVATE_FIXTURES_RESOLVER = "backend/tests/private_fixtures.py"

# Allowed: synthetic or hand-made data files (tests, fixtures, evals, e2e replies), each pinned by the sha256 of its
# content (CRLF read as LF). A deliberate regeneration updates the hash in the same PR, after checking the file holds
# no real data: `python3 scripts/check_public_repo.py --hash <path>` prints the line to put here
# (docs/PUBLIC_REPO_POLICY.md 5).
ALLOWED_TEST_DATA: Dict[str, str] = {
    # M55 Q1: the moved and scanner routes (paths, dependencies, schema hashes) - generated from the code, no data;
    # re-pin after regenerating it (tests/route_table_snapshot.py)
    "backend/tests/fixtures/route_table.json": "8231ad09498409483931a6f0e8eb0503da11ff5d8010abe97f1287fdda37a78e",
    # F&O 8b slice 3: UI screenshots of the auto journal card on made-up trades (e2e/fno8b-slice3-v2.e2e.ts); re-pin
    # after regenerating them
    "frontend/e2e/__screenshots__/fno8b3-journal-v2-1440-dark.png": "445f6eaf5030c874e09efb555e77238bea985e6f0a840b55913d29383c6dd3fc",
    "frontend/e2e/__screenshots__/fno8b3-journal-v2-1440-light.png": "ba6c88488a77bcdc2520ac1ca7721325f0368495043f064ca8088a6ed239cd12",
    "frontend/e2e/__screenshots__/fno8b3-journal-v2-360-dark.png": "fac00f0e47912749f1c483583ce757c07d42252f4e600f988124d1b11147763d",
    "frontend/e2e/__screenshots__/fno8b3-journal-v2-360-light.png": "da1abb6758f9b44aff1ffd876f6c064ef6e94d20f08072c062d56d8f1c889012",
    "frontend/e2e/__screenshots__/fno8b3-journal-v2-390-dark.png": "6193bc560462917918e4733588f743312ef2f75d553cbb1fd65abdf6a7853f5a",
    "frontend/e2e/__screenshots__/fno8b3-journal-v2-390-light.png": "2b430ee531d3413f13a5859303c6989631aaf9bc1a6b4143b90d100f0301dc3e",
    # F&O 8b slice 4: the journal card with its chart snapshot drawn from a made-up price history (e2e/fno8b-slice4-v2.e2e.ts);
    # re-pin after regenerating them
    "frontend/e2e/__screenshots__/fno8b4-journal-chart-v2-1440-dark.png": "5d8f83642a75107ccafeafa40ac21d2eb70c222599c5edb534fc827f91b935e0",
    "frontend/e2e/__screenshots__/fno8b4-journal-chart-v2-1440-light.png": "e1dff6ae447cf32c1371c6d02005e42bcda010facf38bd937b16eb59184b5576",
    "frontend/e2e/__screenshots__/fno8b4-journal-chart-v2-360-dark.png": "f29332dbb977c86bd8eb624c62d7163631f39865ec8dd994997574f09136a702",
    "frontend/e2e/__screenshots__/fno8b4-journal-chart-v2-360-light.png": "177ad3397c620544cce95188ae463e14e1a8b3437807a67d8d4a33566a3bd335",
    "frontend/e2e/__screenshots__/fno8b4-journal-chart-v2-390-dark.png": "e64acfe2d5604b173a9939193ac2746ecaea09513c1ccdf155b8a857d1be76c9",
    "frontend/e2e/__screenshots__/fno8b4-journal-chart-v2-390-light.png": "62f83981eed0910df2678d1f604a7dcb9c5b71cd63f9232470eec992294dca87",
    # ATP-generated goldens on synthetic, seeded inputs (each regeneration site names this list)
    "backend/tests/fixtures/charts/pa_layer_golden.json": "1668c82ad3cd0aeb6d9620192117d36cfc8ce714407cfe15ee0a355674677e11",
    "backend/tests/fixtures/charts/study_parity.json": "790d11653b0056049d8207378cecf1bafce0de40a9e4d0a1ae38bf83518a518b",
    "frontend/src/charting/__fixtures__/pa_layer_golden.json": "1668c82ad3cd0aeb6d9620192117d36cfc8ce714407cfe15ee0a355674677e11",
    "frontend/src/charting/__fixtures__/study_parity.json": "790d11653b0056049d8207378cecf1bafce0de40a9e4d0a1ae38bf83518a518b",
    "frontend/src/charting/__fixtures__/chart_types_golden.json": "f1d1d1178b01d7fcbafee48e33f1c7dd0b3a02b1cbbcbdaf58cba04f5ad94fe5",
    "frontend/src/utils/__fixtures__/indicator_parity.json": "670d39f2624d4b16062fb4cd9d7d0775e10701a76b32f7784ae1892b8e0496a2",
    "backend/tests/golden/c2_default_models.json": "cf7840286077656ce099682a5c52adb0f6bb5a53658963f84efcc9fab27c6d79",
    "backend/tests/fixtures/z10a/structure_base_c5f6a2e.json.gz": "2c5cad35f7a0b3447ff4327eeec6924b3583053cf74f099df70c6ab4e1d635de",
    # synthetic, seeded bars (tests/fixtures/zones/build_fixtures.py) and the hand-made chart band cases
    "backend/tests/fixtures/zones/bn_daily.csv": "c0c34ac840bb0fb8e61aabcf0d191edb1c074c4a5c6ab1366cb60a0204599ec2",
    "backend/tests/fixtures/zones/market_5m.csv": "7420b55dfac3550831281ad6689e513fcfd3c1c673c4b38a8b2fbb749ec4f370",
    "backend/tests/fixtures/zones/session_15m.csv": "03ade33bd2f57085ac25474e723a6892e0bccb623111f689d72a6864d0ae0c43",
    "backend/tests/fixtures/zones/walk_1m.csv": "9d0fa549c0df43a2e8b1dfe087e74e533bfe968de718accd0f003c100695719a",
    "backend/tests/fixtures/zones/chart_bands_cases.json": "66a90beb9b4e75201b937005f46ad53e81494bdc19b5520458b25958eb7c3436",
    "frontend/src/charting/__fixtures__/chart_bands_cases.json": "66a90beb9b4e75201b937005f46ad53e81494bdc19b5520458b25958eb7c3436",
    # Settings redesign A: the AI key-format table and its cases (obviously fake keys only), backend + byte-identical copy
    "backend/tests/fixtures/key_formats.json": "aeddf3fe84caf4396f3104d7d05ca09b04e263351bb9bf043113134110f2e8f1",
    "frontend/src/aiKeys/__fixtures__/key_formats.json": "aeddf3fe84caf4396f3104d7d05ca09b04e263351bb9bf043113134110f2e8f1",
    # MO2A-2: Cloudflare Workers AI answers for the mocked transport, written from its docs source (fake token only)
    "backend/tests/fixtures/cloudflare_workers_ai.json": "31aa8e15b0c249c99edfb103d46fbdfc1a9a41cced1f56f67f9e04a02cc1a48d",
    # Handover 11l: the signal-language cases (made-up texts; symbol names only as examples of what a public text may not
    # carry), backend + byte-identical frontend copy
    "backend/tests/fixtures/signal_language.json": "0350d6d8154d57ec5ef9489e3d56f4239759e547a75dd238479ca7c99b50a5d9",
    "frontend/src/signals/__fixtures__/signal_language.json": "0350d6d8154d57ec5ef9489e3d56f4239759e547a75dd238479ca7c99b50a5d9",
    # Idea library slice 1: the server's own payload of the hand-written idea cards (no market data; synced by a backend test)
    "frontend/e2e/fixtures/ideas_v2.json": "e0c3b0beb07514a1063758737cb17d3ebc8fd021f67f1c89efb9dd054bbca013",
    # Readiness C13: the Learn page and the marketing pages, the server's own hand-written payloads (backend/tests/test_c13_learning.py).
    "frontend/e2e/fixtures/learn_v2.json": "9564cccb9a93d0a6fbfea3f607e1b4cb2225bcbbf9e60ed7a6fb99415a454cf3",
    "frontend/e2e/fixtures/site_v2.json": "3b03dcd1d4b2995aa43919e4c6244943d389a7cef949c8a4b81d32f9d8c82bed",
    # made-up rows in NSE's file layouts (the universe importer's tests)
    "backend/tests/fixtures/universe/EQUITY_L.csv": "c614720d7952f79158ca3470a71b2ef56d653bf83b07646901e1d387a1cc6113",
    "backend/tests/fixtures/universe/SME_EQUITY_L.csv": "8c2c511250ed3789508566e9bd633845f1bf8caad2e0b406a947910f87d50b19",
    "backend/tests/fixtures/universe/eq_etfseclist.csv": "c6303d9f5bfb829376c315ff264219568c33104120cad2a697df04b18f801b48",
    "backend/tests/fixtures/universe/fo_mktlots.csv": "4c557f37797f1b105478ad38eac5cb6ce3e36be38d3e04d6f75542f0a237acbb",
    "backend/tests/fixtures/universe/fo_secban.csv": "63089b8fa563cac692080edefd8be8288c69a810c149b7b139b83406f9a6a1ad",
    "backend/tests/fixtures/universe/ind_close_all_09102026.csv": "d2128ebbe5ef12c6dc22928c11107f94461aea93b90acd98d62ccabbca092a39",
    "backend/tests/fixtures/universe/ind_testindexlist.csv": "544401c201de5479761f00ecc1ddc0e5605aeada400ee74db1db56ace85a9ee3",
    "backend/tests/fixtures/universe/symbolchange.csv": "625b27594c67da488945466f9d9ae4b96847b3fb69316ef04ce565a56dd00a5c",
    "backend/tests/fixtures/universe/symbolchange_noheader.csv": "5a9f35b030909aa202976eb08e24fcf3d488a37e1629d03768d68fee9ecb6c39",
    # WL1: hand-made system-watchlist fixtures (made-up companies, symbols and ISINs; README in the folder).
    "backend/tests/fixtures/universe/wl1/EQUITY_L.csv": "61124b9e2aea1417babff011c1b81fa007729f198e16df5b0b4a167f5bf1e798",
    "backend/tests/fixtures/universe/wl1/classification_full.csv": "ecc32fb6eaf34ca177445dde1be87b524a5873400e2c5054d985c6eca1699f5e",
    "backend/tests/fixtures/universe/wl1/fo_mktlots.csv": "950221cfbc4059acef8576e12439fbbb7d2d2e7e1330d0556c3eea88c9b0f6c3",
    "backend/tests/fixtures/universe/wl1/ind_test500list.csv": "09f92b92f6aa77001068e7a7a29b4296115ebc39256ac85ce74899f97011dc55",
    "backend/tests/fixtures/universe/wl1/ind_test50list.csv": "a1564c2f18c6deb0f7f3e02c32db0749ff73c432374fa9aa5e5ca421071e247e",
    "backend/tests/fixtures/universe/wl1/ind_testbanklist.csv": "7a539735c461e361fca0ce83f46b28015a4814f3d14fb764d9a4a6c6dd5764e6",
    # the private fixtures manifest: paths and sha256 only, no data (backend/tests/private_fixtures.py, owner decision PR-2)
    "backend/tests/private_fixtures_manifest.json": "2aeb33e870fec47b0caa3f317d68e221267a7220c96dec102239e712198b3472",
    # the AI eval sets and their baselines (backend/evals/runner.py)
    "backend/evals/baselines.json": "3c28c69e5964aab11c234bd6cf7608f66c9d327f1dfeacdb64d7f42f2b109a09",
    "backend/evals/sets/injection.jsonl": "8a7ffa3eb5e012784e7b06687b74cdd3f45d32ee4d3ec5537353d5aba6198fd7",
    "backend/evals/sets/qa.jsonl": "ed0530199bad39021edf8e4777700dedb99619c0eeeaca623dcf958d015e22ad",
    "backend/evals/sets/tool_args.jsonl": "578efa3d69f7d860f221068273a074b018e7998b71bdf8c55d5bdbd66d7f862b",
    "backend/evals/sets/copilot_jobs.jsonl": "571e6d59bc541582594776320250484c80712850ed0d77465d64495f1e498a0a",
    "backend/evals/sets/copilot_safety.jsonl": "6957614b58ca737a77f1445e2d3bd0a13fa5c3063c829e75506e6043a3a37287",
    "backend/evals/sets/copilot_tools.json": "39bbfbb4e1e2e94c6bec38490ca623d128c7324b10691e781a6b93fce5cf3744",
    # M72 CP-0: the trader eval (backend/evals/trader_eval.py) - 60 questions, the safety block, the language lists, the recorded
    # synthetic tool results (evals/build_trader_fixtures.py) and the baseline (raised only)
    "backend/evals/sets/copilot_trader.jsonl": "b402a544489fd33a1c894d39eb1e397f64b78b5984e7f617d699786da893da37",
    "backend/evals/sets/copilot_trader_safety.jsonl": "bbfa1fcc7a4df13f8102970366740719b011738590ecc089b3b3c60fdff2e2bf",
    "backend/evals/sets/copilot_trader_lang.json": "93a1b22c0fe6e35d6815969ace2666767f3c97e736591e4cfe9fca91b221f0f0",
    "backend/evals/sets/copilot_trader_tools.json": "c866475c9df9e7f8d3d5c946da8d91d107e47e7ccc07430c8477799505ddd440",
    "backend/evals/trader_baseline.json": "98638992ae9740203b295a1ec8d07f8fca0ec54b23c727c0ab1d9110e7b2c13c",
    "frontend/e2e/fixtures/copilot_tools_v2.json": "37c00546e26cc0cab4b2af0c38ae6041a630d57790be87397fa186502f3e4502",
    # hand-written API replies for the Playwright tests
    "frontend/e2e/fixtures/ack.json": "42357f9971519912aa2ac0bab5a4498de677bae7aea2270e9e854057b4b11361",
    "frontend/e2e/fixtures/actions.json": "49e8cbfacbd96f95bc73a90691ae343bd487140c7ba30b5954031a1a2d96e547",
    "frontend/e2e/fixtures/actions_proposed.json": "7ab4b210c637af15cc426efabca6f83fb94a5ff0b86879d1dd539019d99f91c9",
    "frontend/e2e/fixtures/brief.json": "f0791d9852a6412195c3735959059e32f526477c8b78746e176504b443324aa9",
    "frontend/e2e/fixtures/coach.json": "27695969bdfbc8781b68e088fcc86315926ab34a4e9e68350e05a3e1a04bd814",
    "frontend/e2e/fixtures/copilot_reply.json": "9d0013cced7fe29e99d3c2c25e231d96bee15cbd4960f169194abc1247d77bcd",
    # UI2-2: hand-written, made-up dashboard replies for the v2 dashboard screenshots (frontend/e2e/dashboard-v2.e2e.ts)
    "frontend/e2e/fixtures/dashboard_v2.json": "6e9629add17dd3b9801e2db91a4aebc984b2a00055c5e7b137e93653782a110f",
    # Part 1B: hand-made Kotak Neo API responses in the SDK's documented shapes (made-up ids and numbers; tokens empty).
    "backend/tests/fixtures/kotak_neo/recorded.json": "378d6a817a689a938577f5114701c5e7448053a5f89fcc6be4f9e6df5cb91da3",
    # Part 1B: hand-made Groww API responses in the shapes of Groww's API reference (made-up ids and numbers; tokens empty).
    "backend/tests/fixtures/groww/recorded.json": "048c67ff5ce98dd7cb64dc0ca6fec75dbdfe6b355e4b596c6b456c96dca1040c",
    # P2b: P2a's broker catalog (static, public broker names and how-to steps; no account data).
    "frontend/e2e/fixtures/broker_catalog.json": "075bd516ec146878bbca8bbfcfe3db046527510c7a080f44f71e58eb387eeb20",
    "frontend/e2e/fixtures/drafts.json": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
    "frontend/e2e/fixtures/interview_plan.json": "3b582f587c25dfe0f99ab25fafb34f77d458e1910cbaba5747c964997fbdb42c",
    "frontend/e2e/fixtures/interview_start.json": "b702f8734442fd26183eb52ae508de6e55edfc0df57ee406c75776cc1ecc4453",
    "frontend/e2e/fixtures/me.json": "40267b747ce992144d3a64dab5a8800a4ba51db1451284e13f03af88cb56d7cf",
    "frontend/e2e/fixtures/memory.json": "7c3eb9866eef9c03efa6d3c3b489c3f988c52244a95a51e60618da5564ae54fa",
    "frontend/e2e/fixtures/news_items.json": "81d5a6eb8342a64eb397baac6de4f1378c54707f78e57380e5f87b28b3d9f7ee",
    "frontend/e2e/fixtures/news_status.json": "be5ac693f289f43bc2bfeb790c22a9390a985a0715f8193670de81c64245e7bd",
    "frontend/e2e/fixtures/notifications.json": "be5ded0116ca5c0f8f71caa2170c75eba590783ad737fdd99d14db74c4af4faa",
    "frontend/e2e/fixtures/option_chain_v2.json": "c37438deaa26205fcc03016050fae76b6e181a2f9911587e4ac51d81e2a98848",
    "frontend/e2e/fixtures/option_chain_charts.json": "26645da956042b696609fa5515bb62ca052a83ec4d6f6f41576790fedb1aaa25",
    "frontend/e2e/fixtures/options_builder.json": "bf6df3ea9f5caddb5e3acc1d2f8360875a3cd72550f194156b21266fd550e1f0",
    "frontend/e2e/fixtures/provider.json": "6aabdd6f537fd8ce0a735b8ec21ed11e8f590ea3718263cc470f0916e53c7b32",
    "frontend/e2e/fixtures/setup_v2.json": "8cedb077243878f1e3ee50c22996a933359db0a0127084b2eb471c637a54261b",
    # P3: hand-written, made-up replies for the front page and the go-live checklist (no real account, broker or token)
    "frontend/e2e/fixtures/front_page.json": "a2a51de38eec31a2a2735557596a4599d3a1efd73c4bd39d929ca822447d850d",
    "frontend/e2e/fixtures/signals_v2.json": "8a8c59c855e923dd02db351c5f8753d26851310cd5af5e2f405b9c78339338a8",
    "frontend/e2e/fixtures/system_status.json": "1e6eb9fa4f6aadbabc6128522a423a6697471cb4f71dd6c548c0b4bbe788bb91",
    "frontend/e2e/fixtures/thesis.json": "fafc2234d7b94bb57a0a860e95d3d3cf8052796ef9d9011db2d12cf486b3bac7",
    "frontend/e2e/fixtures/thesis_history.json": "f3226989acc68ddf2bbfa69ff8a5353011a90a480befc57c0bfa0c988ccd5a6c",
    "frontend/e2e/fixtures/thesis_report.json": "e85180cb3f957bf048f9bffe63e3237bb760333376e255f1ae76f6d9df2e287d",
    "frontend/e2e/fixtures/trading_v2.json": "c9fc66383c29a2a5dc38d9de57bff8d27b9ba75b74578fa839b2eb420a73afef",
    "frontend/e2e/fixtures/mytrades_cards_v2.json": "4d0ba8e0bd9fcf0e3117746ad9169e832f209600858c79df807b8ff92a8fa82d",
    "frontend/e2e/fixtures/copilot_chat_v2.json": "d844e9cfe7294dd7a5faf64e21b751ce48b8085739ac8dfd5c7f6bf67d3dc22f",
    # M68 PR-0: Settings > Trade settings - the registry's own replies (backend/app/me/trade_settings.py view(); synthetic)
    "frontend/e2e/fixtures/trade-settings-user.json": "9858c0a59abe38561dfb867251fdd871c7c32a948905caa9f1d36aed75dba06d",
    "frontend/e2e/fixtures/trade-settings-admin.json": "7dcd35f6dc73fa65f32acfd940edbe8eb0d8d25dc4ac8c357b5cc398abad01d5",
}

# Public data rebuilt by automation, allowed by path (no hash: it changes on every refresh). Each entry says its source.
ALLOWED_PUBLIC_DATA: Dict[str, str] = {
    "backend/app/instruments/data/nse_index_expiries.csv":
        "NSE index expiry calendar, rebuilt weekly from NSE's public F&O bhavcopies by .github/workflows/nse-expiries.yml",
}

_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", "coverage", ".pytest_cache",
              ".mypy_cache", ".ruff_cache", "playwright-report", "test-results", "storybook-static"}
_MAX_BYTES = 8 * 1024 * 1024
_BINARY_EXT = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".bmp", ".ico", ".icns", ".tif", ".tiff", ".woff", ".woff2",
                         ".ttf", ".otf", ".eot", ".pdf", ".zip", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".parquet", ".feather", ".arrow",
                         ".pkl", ".npy", ".npz", ".h5", ".mp3", ".mp4", ".webm", ".wav", ".ogg", ".sqlite", ".db", ".xlsx", ".xls",
                         ".docx", ".pptx", ".jar", ".so", ".dylib", ".dll", ".exe", ".bin", ".pyc", ".wasm", ".jks", ".keystore",
                         ".p12", ".pfx", ".pepk"})

# --- content patterns -------------------------------------------------------------------------------------------------
_IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d]|\.\d)")
# A version after a known browser token ("Chrome/120.0.0.0" in a user agent) is not an address; any other "x/1.2.3.4" is.
_BROWSER_VERSION = re.compile(r"(?<![A-Za-z])(?:Mozilla|AppleWebKit|Gecko|Chrome|Chromium|HeadlessChrome|CriOS|Firefox|FxiOS"
                              r"|Safari|Version|Edg|EdgA|EdgiOS|Edge|OPR|Opera|Trident|MSIE|SamsungBrowser|YaBrowser|Mobile)/$")
# Four numbers joined by one of . - _ / : or a space ("ip-a-b-c-d", "a_b_c_d"): checked against the production hash only.
_IP_ANY_SEPARATOR = re.compile(r"(?<!\d)(?=(\d{1,3})[-_. /:](\d{1,3})[-_. /:](\d{1,3})[-_. /:](\d{1,3})(?!\d))")
_EMAIL = re.compile(r"(?<![A-Za-z0-9._%+])[A-Za-z0-9_][A-Za-z0-9._%+-]*@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}(?![A-Za-z0-9-])")
_TELEGRAM_TOKEN = re.compile(r"(?<![\d])\d{8,10}:[A-Za-z0-9_-]{35}(?![A-Za-z0-9_-])")
_CHAT_ID = re.compile(r"(?i)(?:chat_?id|telegram_?(?:user_?|chat_?)?id)[\"']?\s*[:=]\s*[\"']?-?\d{7,}")
_SUPERGROUP_ID = re.compile(r"(?<![\d.])-100\d{10}(?!\d)")
# A private key header, written so this file does not hold one itself.
_PRIVATE_KEY = re.compile("-{5}BEGIN (?:(?:RSA|DSA|EC|OPENSSH|ENCRYPTED|PGP) )?PRIVATE KEY(?: BLOCK)?-{5}")

# --- path patterns ----------------------------------------------------------------------------------------------------
_SECRET_EXT = frozenset({".pem", ".key", ".ppk", ".jks", ".keystore", ".p12", ".pfx", ".pepk"})
_SECRET_NAMES = frozenset({"google-services.json", "googleservice-info.plist", "keystore.properties", "signing.properties"})
_SECRET_JSON = re.compile(r"(?:service[_-]?account|credentials).*\.json$", re.IGNORECASE)
# .npmrc / .pypirc: a token or password with a literal value (a ${VARIABLE} reference is fine).
_NPMRC_TOKEN = re.compile(r"(?:_authToken|_auth|_password)\s*=\s*(?!\$\{)\S")
_PYPIRC_TOKEN = re.compile(r"^\s*password\s*[=:]\s*(?!\$\{|%\()\S", re.IGNORECASE)
# The KB, the Method Pack and the Chart Reader method stay out of this public repository (owner decision PR-1: the KB,
# the Method Pack and the reader live in the private package).
_METHOD_PATH = re.compile(r"(?:^|/)docs/knowledge/|method[ _-]?pack|(?:^|/)knowledge[ _-]?base[^/]*$|method[ _-]?notes"
                          r"|(?:^|/)chart[ _-]?reader[ _-]?method[^/]*$", re.IGNORECASE)
_ENV_TEMPLATES = (".example", ".sample", ".template")
_ENV_VALUE = re.compile(r"^\s*(?:export\s+)?[A-Za-z_][A-Za-z0-9_]*\s*=\s*(?P<v>.*)$")
_COMPRESSED = (".gz", ".zip", ".bz2", ".xz", ".7z", ".tgz")
_DATA_EXT = frozenset({".csv", ".tsv", ".json", ".jsonl", ".parquet", ".xlsx", ".xls", ".feather", ".pkl", ".npy", ".npz"})
_TABULAR_EXT = frozenset({".csv", ".tsv", ".parquet", ".xlsx", ".xls", ".feather", ".pkl", ".npy", ".npz"})
_IMAGE_EXT = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".pdf"})
_FIXTURE_DIR = re.compile(r"(?:^|/)(?:fixtures|__fixtures__)/")
# Any data or compressed file here must be listed (ALLOWED_TEST_DATA).
_TEST_DATA_DIR = re.compile(r"^(?:backend/tests|backend/evals|frontend/test|frontend/e2e)/|(?:^|/)__fixtures__/")
_TEST_DATA_EXT = _DATA_EXT | frozenset({".arrow", ".h5", ".db", ".sqlite"})
# Tabular and binary data files: listed wherever they are (ALLOWED_TEST_DATA or ALLOWED_PUBLIC_DATA).
_ANYWHERE_DATA_EXT = frozenset({".csv", ".tsv", ".xlsx", ".xls", ".parquet", ".feather", ".pkl", ".npy", ".npz", ".h5", ".arrow"})
_GOLDEN = re.compile(r"golden|parity", re.IGNORECASE)
_REAL_DATA_NAME = re.compile(r"journal|trade_?book|trade_?log|trade_?history|contract_?note|p_?n_?l_?report|chart_?export"
                             r"|tradingview|ledger", re.IGNORECASE)


def _git_files(root: Path) -> Optional[List[str]]:
    try:
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                             capture_output=True, check=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    top = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip()
    if not top or Path(top).resolve() != root.resolve():
        return None                         # a sub-directory of another repository: walk it instead
    return sorted({p for p in out.decode("utf-8", "surrogateescape").split("\0") if p})


def _walk(root: Path) -> List[str]:
    found: List[str] = []
    for d, dirs, names in os.walk(root):
        dirs[:] = [x for x in dirs if x not in _SKIP_DIRS]
        for name in names:
            found.append(Path(d, name).relative_to(root).as_posix())
    return sorted(found)


def files(root: Path) -> List[str]:
    return _git_files(root) or _walk(root)


def _suffixes(rel: str) -> Tuple[str, str]:
    """(the data suffix, the outer suffix): "a/b.json.gz" -> (".json", ".gz"); "a/b.csv" -> (".csv", ".csv")."""
    path = PurePosixPath(rel.lower())
    outer = path.suffix
    if outer in _COMPRESSED:
        return PurePosixPath(path.stem).suffix or outer, outer
    return outer, outer


def is_production_ip(ip: str) -> bool:
    return hashlib.sha256(ip.encode("ascii")).hexdigest() in PRODUCTION_IP_SHA256


def production_ip_count(line: str) -> int:
    """How many times the production IP appears in the line, dotted or with - _ or space between the numbers."""
    return sum(1 for m in _IP_ANY_SEPARATOR.finditer(line) if is_production_ip(".".join(m.groups())))


def content_sha256(data: bytes) -> str:
    """The sha256 the fixture lists pin: of the file's bytes, CRLF read as LF (a Windows checkout gives the same)."""
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def is_flagged_ip(ip: str) -> bool:
    """True for a public IPv4 address that is neither a documentation address nor in ALLOWED_IPS."""
    try:
        addr = ipaddress.IPv4Address(ip)
    except ValueError:
        return False                        # "300.1.1.1" and the like: a version, not an address
    if ip in ALLOWED_IPS or any(addr in net for net in DOC_NETWORKS):
        return False
    return addr.is_global or addr.is_multicast


def is_allowed_email(address: str) -> bool:
    address = address.lower()
    return address in ALLOWED_EMAILS or bool(_EXAMPLE_DOMAIN.search(address.rsplit("@", 1)[1]))


def _redact_email(address: str) -> str:
    local, domain = address.rsplit("@", 1)
    return f"{local[:1]}***@{domain}"


def allow_line(rel: str, data: bytes) -> str:
    """The ALLOWED_TEST_DATA line that pins this content."""
    return f'"{rel}": "{content_sha256(data)}",'


def _pin_hint(rel: str, data: Optional[bytes]) -> str:
    if data is None:
        return f"run python3 scripts/check_public_repo.py --hash {rel}"
    return f"add this line to ALLOWED_TEST_DATA in scripts/check_public_repo.py: {allow_line(rel, data)}"


def path_problems(rel: str, data: Optional[bytes] = None) -> List[str]:
    """Findings from the file's path (secret file types, the KB / method, fixtures / real-data files). A listed file
    passes here; its content is checked against its pinned sha256 by `listed_data_problems`. With the file's bytes, a
    data-file finding also prints the line that would pin it."""
    problems: List[str] = []
    name = PurePosixPath(rel).name.lower()
    data_ext, outer = _suffixes(rel)
    if outer in _SECRET_EXT or name in _SECRET_NAMES or _SECRET_JSON.search(name):
        problems.append(f"{rel}: a secret file type ({name}) - keys, keystores and service files never go into git")
    if _METHOD_PATH.search(rel):
        problems.append(f"{rel}: the KB / Method Pack / Chart Reader method - not in this public repository "
                        f"(owner decision PR-1: the KB, the Method Pack and the reader live in the private package)")
    if rel in ALLOWED_TEST_DATA or rel in ALLOWED_PUBLIC_DATA:
        return problems
    is_data = data_ext in _DATA_EXT or outer in _COMPRESSED
    reason = None
    if _TEST_DATA_DIR.search(rel) and (data_ext in _TEST_DATA_EXT or outer in _COMPRESSED):
        reason = "a new data file in the tests, fixtures or evals (not in ALLOWED_TEST_DATA)"
    elif data_ext in _ANYWHERE_DATA_EXT or outer in _ANYWHERE_DATA_EXT:
        reason = "a tabular or binary data file (not listed)"
    elif is_data and _GOLDEN.search(rel):
        reason = "a golden / parity fixture"
    elif _FIXTURE_DIR.search(rel) and (data_ext in _TABULAR_EXT or outer in _COMPRESSED or outer in _IMAGE_EXT):
        reason = "a data, compressed or image file in a fixtures folder"
    elif (is_data or outer in _IMAGE_EXT) and _REAL_DATA_NAME.search(name):
        reason = "a trade journal / tradebook / chart export"
    if reason:
        problems.append(f"{rel}: {reason} - real data and real-data goldens go to the private fixtures store "
                        f"(docs/PUBLIC_REPO_POLICY.md). If it is synthetic or hand-made, that is fine: {_pin_hint(rel, data)}")
    return problems


def listed_data_problems(rel: str, data: bytes) -> List[str]:
    """An allowed data file whose content no longer matches its pinned sha256."""
    pinned = ALLOWED_TEST_DATA.get(rel)
    if pinned is None or content_sha256(data) == pinned:
        return []
    return [f"{rel}: test data with new content - check it holds no real data, then replace its line in ALLOWED_TEST_DATA "
            f"in scripts/check_public_repo.py with: {allow_line(rel, data)}"]


def _plain_relative(rel: str) -> bool:
    """A manifest path: relative, POSIX, no empty, '.' or '..' segment."""
    return bool(rel) and "\\" not in rel and not PurePosixPath(rel).is_absolute() and \
        all(p not in ("", ".", "..") for p in rel.split("/"))


def private_fixtures(root: Path) -> Dict[str, str]:
    """The private fixtures manifest's {path: sha256 of the raw bytes}. Empty when the tree has neither the manifest
    nor the resolver (another tree, e.g. in the tests). Raises ValueError when the resolver is there without the
    manifest, or when the manifest cannot be read as {"files": {relative path: sha256}}."""
    path = root / PRIVATE_FIXTURES_MANIFEST
    if not path.is_file():
        if (root / PRIVATE_FIXTURES_RESOLVER).is_file():
            raise ValueError(f"{PRIVATE_FIXTURES_MANIFEST}: missing, but {PRIVATE_FIXTURES_RESOLVER} is in the tree - "
                             f"without it the guard cannot refuse the private fixtures")
        return {}
    try:
        listed = json.loads(path.read_text(encoding="utf-8"))["files"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"{PRIVATE_FIXTURES_MANIFEST}: unreadable ({exc.__class__.__name__})") from exc
    if not isinstance(listed, dict) or not all(isinstance(v, str) for v in listed.values()):
        raise ValueError(f"{PRIVATE_FIXTURES_MANIFEST}: 'files' is not a mapping of path to sha256")
    bad = sorted(str(k) for k in listed if not _plain_relative(str(k)))
    if bad:
        raise ValueError(f"{PRIVATE_FIXTURES_MANIFEST}: {len(bad)} path(s) not plain relative paths (absolute or with '..')")
    return {str(k): v.lower() for k, v in listed.items()}


def private_fixture_problems(rel: str, private: Dict[str, str], data: Optional[bytes] = None) -> List[str]:
    """A file the private fixtures manifest lists, back in the tree; with the file's bytes, also a copy of one under
    another name (the same raw sha256)."""
    if rel in private:
        return [f"{rel}: a private fixture ({PRIVATE_FIXTURES_MANIFEST}) - Trade-derived goldens live only in the private "
                f"fixtures store (owner decision PR-2, docs/PUBLIC_REPO_POLICY.md section 5); remove it from the tree"]
    if data is not None and private:
        digest = hashlib.sha256(data).hexdigest()
        for listed, pinned in sorted(private.items()):
            if digest == pinned:
                return [f"{rel}: a copy of the private fixture {listed} (same sha256) - it lives only in the private "
                        f"fixtures store (owner decision PR-2, docs/PUBLIC_REPO_POLICY.md section 5); remove it from the tree"]
    return []


def config_token_problems(rel: str, text: str) -> List[str]:
    """An .npmrc / .pypirc that holds a token or password."""
    name = PurePosixPath(rel).name.lower()
    pattern = _NPMRC_TOKEN if name == ".npmrc" else _PYPIRC_TOKEN if name == ".pypirc" else None
    if pattern is None:
        return []
    for n, line in enumerate(text.splitlines(), 1):
        if pattern.search(line):
            return [f"{rel}:{n}: {name} with a token or password (redacted) - use a ${{VARIABLE}} from the secret store"]
    return []


def _is_env_file(name: str) -> bool:
    return (name == ".env" or name.startswith(".env.") or name.endswith(".env")) and not name.endswith(_ENV_TEMPLATES)


def env_problems(rel: str, text: str) -> List[str]:
    """An .env file (not a template) that holds a value."""
    for n, line in enumerate(text.splitlines(), 1):
        m = _ENV_VALUE.match(line)
        if m and m.group("v").split("#", 1)[0].strip().strip("\"'"):
            return [f"{rel}:{n}: an .env file with values - real settings live only on the server or in the secret store"]
    return []


def text_problems(rel: str, text: str) -> List[str]:
    """Findings in the file's text. Values are never echoed in full."""
    problems: List[str] = []
    for n, line in enumerate(text.splitlines(), 1):
        for _ in range(production_ip_count(line)):
            problems.append(f"{rel}:{n}: the production server's IP address - write <server-ip> (docs) or a "
                            f"documentation address such as 203.0.113.10 (tests)")
        for m in _IPV4.finditer(line):
            ip = m.group(0)
            if is_production_ip(ip):
                continue                    # reported above
            if not _BROWSER_VERSION.search(line[:m.start()]) and is_flagged_ip(ip):
                problems.append(f"{rel}:{n}: public IPv4 address {ip} - use a documentation address (192.0.2.x, "
                                f"198.51.100.x, 203.0.113.x) or a placeholder")
        for m in _EMAIL.finditer(line):
            if rel not in EMAIL_EXEMPT_FILES and not is_allowed_email(m.group(0)):
                problems.append(f"{rel}:{n}: e-mail address {_redact_email(m.group(0))} - use an example.com address")
        if _TELEGRAM_TOKEN.search(line):
            problems.append(f"{rel}:{n}: a Telegram bot token (redacted) - rotate it with @BotFather if it was real")
        if _CHAT_ID.search(line) or _SUPERGROUP_ID.search(line):
            problems.append(f"{rel}:{n}: a Telegram chat id (redacted) - use a short fake id such as 987654")
        if _PRIVATE_KEY.search(line):
            problems.append(f"{rel}:{n}: a private key header - a private key never goes into git")
    return problems


def _read(path: Path, rel: str, notices: List[str]) -> Optional[str]:
    try:
        if not path.is_file() or PurePosixPath(rel.lower()).suffix in _BINARY_EXT:
            return None
        if path.stat().st_size > _MAX_BYTES:
            notices.append(f"{rel}: over {_MAX_BYTES // (1024 * 1024)} MB - checked by path only")
            return None
        return path.read_bytes().decode("utf-8", "replace")
    except OSError:
        return None


def scan(root: Path) -> Tuple[List[str], List[str]]:
    """(findings, notices). Findings fail the check; notices do not."""
    root = root.resolve()
    problems: List[str] = []
    notices: List[str] = []
    try:
        private = private_fixtures(root)
    except ValueError as exc:
        private = {}
        problems.append(str(exc))
    for rel in files(root):
        if not (root / rel).is_file():
            continue                        # deleted in the work tree, not yet committed
        try:
            raw: Optional[bytes] = (root / rel).read_bytes() if private else None
        except OSError:
            raw = None
        problems.extend(private_fixture_problems(rel, private, raw))
        found = path_problems(rel)
        if any("--hash" in p for p in found):
            try:
                found = path_problems(rel, (root / rel).read_bytes())    # with the line that would pin it
            except OSError:
                pass
        problems.extend(found)
        if rel in ALLOWED_TEST_DATA:
            try:
                problems.extend(listed_data_problems(rel, (root / rel).read_bytes()))
            except OSError:
                pass
        text = _read(root / rel, rel, notices)
        if text is None:
            continue
        if _is_env_file(PurePosixPath(rel).name.lower()):
            problems.extend(env_problems(rel, text))
        problems.extend(config_token_problems(rel, text))
        problems.extend(text_problems(rel, text))
    return problems, notices


def check(root: Path) -> List[str]:
    """Every finding as 'path[:line]: text'. Empty = clean."""
    return scan(root)[0]


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--root", default=str(Path(__file__).resolve().parent.parent), help="the repository root (default: this script's repository)")
    parser.add_argument("--hash", nargs="+", metavar="PATH",
                        help="print the ALLOWED_TEST_DATA line that pins each file (a path from here or from the root)")
    args = parser.parse_args(list(argv) if argv is not None else None)
    root = Path(args.root).resolve()
    if args.hash:
        for raw in args.hash:
            path = Path(raw) if Path(raw).exists() else root / raw
            try:
                rel = path.resolve().relative_to(root).as_posix()
                data = path.read_bytes()
            except (OSError, ValueError) as exc:
                print(f"check_public_repo: {raw}: {exc}", file=sys.stderr)
                return 2
            print("    " + allow_line(rel, data))
        return 0
    problems, notices = scan(root)
    for note in notices:
        print(f"check_public_repo: notice - {note}", file=sys.stderr)
    if problems:
        print(f"Public repository guard: {len(problems)} finding(s) (scripts/check_public_repo.py, docs/PUBLIC_REPO_POLICY.md). "
              f"This repository is public: everything committed is public.", file=sys.stderr)
        for p in problems:
            print("  " + p, file=sys.stderr)
        return 1
    print(f"check_public_repo: OK - no server IP, private e-mail, Telegram id, key or new real-data fixture "
          f"({len(private_fixtures(root))} private fixtures kept out of the tree, "
          f"{len(ALLOWED_TEST_DATA)} pinned test data files, {len(ALLOWED_PUBLIC_DATA)} public data file(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
