# Channel liveness survey

Survey date: 2026-10-03. Re-survey before wiring any new provider: this
file expires in 30 days (2026-11-02). A platform below qualifies only if
all of these held on survey day: reachable, active, payout evidence in
the last 30 days, and terms allowing this use. Anything weaker ships as
generic/manual only.

## Bounty boards

### GrantFox — QUALIFIED with conditions

- URL: https://grantfox.xyz/
- API docs URL: none public. Access is the site plus a GrantFox MCP
  server for AI clients (see Terms 4B below). No REST endpoint for open
  issues was found on survey day.
- Payout evidence: live site counter reads $201,324 USDC total
  distributed (seen 2026-10-03), details at
  https://analytics.grantfox.xyz. Backed by Stellar Community Fund
  Build Awards; campaigns ran 2026-06-15 ($60,000 pool) and 2026-07
  (second campaign). Escrow via Trustless Work, 0.3% fee per payout.
- Payout status machine-readable: partial. Analytics page plus on-chain
  USDC on Stellar; no documented per-issue payout API.
- Terms on automation:
  https://docs.grantfox.xyz/additional-info/terms-and-conditions
  (updated 2026-07-16). Section 5.4 bans bots/automation that spam
  applications, issues, or PRs. Section 4B allows AI clients through the
  MCP but puts full review responsibility on the human (4B.2) and bans
  exceeding permissions or creating spam (4B.3).
- Conditions for our use: read-only discovery plus human-approved
  actions only. Automated applying without human review would violate
  5.4. Our approval gate plus manual submit path complies. Single
  assignee per paid issue (Terms 7.2.1); multi-assignee issues are
  ineligible for payment, so the discoverer must skip them.

### Algora — NOT qualified for automated discovery

- API docs live: https://api.docs.algora.io/ (bounty CRUD, escrowed
  USD payouts, public per-org GET endpoints).
- Against: no solver-facing open-bounty feed was found (the
  /bounties listing page returns 404); the only completed-bounty
  evidence at hand is months old
  (https://algora.io/polarsource/bounties?status=completed, $200 sample
  from ~8 months ago). No payout-in-30-days evidence on survey day.
- Ships as: nothing automated. Revisit if a public open-bounty feed
  plus fresh payout evidence appears.

### Polar (ex-polarsource) — EXCLUDED

- Pivoted to billing/merchant-of-record (https://polar.sh/). Bounty
  program dead; old links redirect. Do not build on it.

### Superteam Earn agent listings — zero live on survey week

- Our own watcher (`superteam_poller.py`) reported live=0 on
  2026-10-02 and 2026-10-03. Nothing to discover; keep the watcher,
  do not build a channel on it until listings return.

## Grants

No grant-round platform qualified on survey day. GrantFox campaigns
function as rounds but have no machine-readable round feed, and no
other round publisher with deadline-plus-amount data was verified.
This type ships with the generic RSS/Atom provider only
(`[channels.grants] feeds`), plus manual round entry. Stellar
Community Fund rounds (https://communityfund.stellar.org) are noted
as a candidate for manual entry, not verified machine-readable.

## Client work: inbound and job feeds

### RemoteOK public API — QUALIFIED as feed source (not a payer)

- URL: https://remoteok.com/api (JSON, no key). RSS twin is dead
  (HTTP 410, confirmed 2026-10-03 by our own fetcher and third
  parties).
- Terms ride inside the response (`.[0].legal`): use allowed with a
  followed backlink to the posting URL and Remote OK named as source;
  logo use forbidden. Attribution is a condition of access.
- Limits: newest ~100 postings only, ~a week of history, no search or
  pagination, tags filter ignored. Harvest by polling and diffing on
  `id`; records carry `url` and `apply_url` for citation.
- Every candidate from this feed must cite the public posting URL.
  It is a lead source, never payment evidence.

### Upwork job RSS — DEAD

- Public RSS returns HTTP 410 (confirmed by our own
  `upwork_rss_hunter.py`). Do not build on it.

### Inbound email — QUALIFIED (own mailbox)

- No third party involved. IMAP folder plus subject/body regex filters
  from config. Every candidate must cite the inbound message id. This
  is the primary client_inbox source until a second feed qualifies.

## Zero-qualifier rule

If a type has zero qualifying platforms, its channel still ships with
the generic part only (manual entry, RSS provider, mailbox). That is
the case for grants on survey day. Stop and write to QUESTIONS.md if
no platform qualifies in any type; that did not happen (two
qualified: GrantFox discovery, RemoteOK feed, plus own mailbox).
