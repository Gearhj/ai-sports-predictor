# AISP vs the competition (2026-10-25)

James asked: do competitors have something we don't, what is uniquely theirs, and what can we borrow?
Sources are the products' own pages, app-store listings and third-party reviews (listed at the end). Most accuracy
claims in this market are self-reported and unaudited; none is repeated here as fact.

## Who they are, in one line each

| Product | What it is | Price (reported) |
|---|---|---|
| Action Network PRO | Odds, public-betting and money percentages, "Sharp Action" signals, systems, bet tracking, push alerts | ~$30/mo |
| OddsJam | +EV, arbitrage and middles finder against a sharp/no-vig line, 90+ books, one-click to the book | ~$199–499/mo |
| Unabated | Vig-free "Unabated Line" consensus, odds screens, CLV and hold calculators, prop projections | ~$49–99/mo |
| Rithmm | No-code "build your own model" by picking and weighting factors, backtest, Smart Signals | ~$30–100/mo |
| Dimers Pro | Monte Carlo game simulations, best bets with edge %, Parlay Picker, props, "Dimebot" AI chat | ~$10/week |
| Leans.ai (Remi) | 2–6 AI picks a day with unit sizing; publishes a units record with CLV | ~$49–329/mo |
| Pikkit | Bet tracker that syncs 30+ sportsbook accounts, CLV analytics, social follow/tail, leaderboards | free + Pro |
| Outlier / BettingPros / Props.cash | Player-prop research: hit rates (L5/L10), splits, prop projections, line movement | ~$10–40/mo |

## What they have that AISP does not

1. **Public betting and money percentages, "sharp money" flags** (Action Network). Needs a paid handle data source
   (Sports Insights). We cannot copy it, but we can build our own sharp-move detector from odds we already collect.
2. **Alerts.** Game start, scores, bet final, line moved past a threshold (Action Network, Dimers). AISP has none.
3. **Closing line value (CLV) on every pick** (Pikkit Pro, Leans, MVP Capping, Unabated calculators). CLV is the
   industry's credibility metric: did you get a better price than the market's final word? AISP records every
   decision price and collects closing prices, but never shows CLV.
4. **+EV and arbitrage screens against a no-vig consensus** (OddsJam, Unabated). AISP has the consensus (crowd
   research) and Shop the Line, but no "this book is above fair value right now" list.
5. **Build-your-own model** (Rithmm). Users choose factors and weights, then see a backtest.
6. **Player props research** (Outlier, BettingPros, Props.cash, Dimers). AISP's prop collector exists but is
   inactive and needs Odds API credit (James's spend).
7. **Spreads, totals and many more books** (everyone). AISP collects moneylines only.
8. **Sportsbook account sync** (Pikkit). Needs partner integrations; not realistic for us now.
9. **An AI chat assistant** (Dimebot, Pikkit AI). AISP's "Ask the Booth" works in test mode; production needs a
   backend endpoint and James's yes on the API spend (pennies per question).
10. **Native mobile apps.** AISP is a responsive web app.

## What is specifically theirs (hard to copy)

- Action Network: real ticket and handle data, plus a media brand and expert content.
- OddsJam: speed and breadth of the odds feed (dozens of books, sub-minute refresh) and one-click bet links.
- Pikkit: sportsbook account sync and a social graph of bettors.
- Rithmm: the no-code model builder as the core product.
- Dimers: "we simulate every game thousands of times" as its brand.

## What AISP has that none of them do

- **An immutable, public paper ledger of every decision, including the ones it declined**, with the reason in its own
  words (Daily Decisions, Verdict Wall, The Record). Competitors publish win rates; we publish every call and the cap
  that stopped it.
- **Honesty about the market.** The Truth Meter shows calibration per model, and Model vs Market says the market is
  ahead right now. No competitor shows where its own model loses.
- **Pipeline transparency.** Mission Control shows every gate a game passes, live.
- **Live View.** Reconstructed plays on a field, the Attack Map against the model, Coach's View play-calling
  tendencies, the fourth-down decision map and turnover callouts. Competitors stop at a live win-probability line.
- **10,000 Nights** on the user's own slip (model vs market Monte Carlo of their night), and the 10-point gap rule
  framed as a warning, not an edge.

## What to borrow, in order of value per effort

| # | Feature | Borrowed from | Needs | Effort |
|---|---|---|---|---|
| 1 | **Beat the Close**: CLV on every AISP decision and every My Picks leg, with a running "% of picks that beat the close" | Pikkit, Leans, Unabated | Closing prices we already collect | Small |
| 2 | **Alerts** for slip games and AISP bets: start, score swing, final, price moved | Action Network | Browser notifications (no cost) | Small |
| 3 | **Market Movers**: our own steam / sharp-move detector (many books move the same way within minutes) | Action Network "Sharp Action" | Our odds snapshots | Medium |
| 4 | **Value Finder**: books priced above the no-vig consensus right now, with the 10-point warning | OddsJam, Unabated | Existing quotes and consensus | Small |
| 5 | **Model Lab**: blend sliders (AISP model, market, Elo, rest, injuries) with an instant backtest on our settled history | Rithmm | Settled decisions and forecasts | Medium; a strong HOLY SHIT candidate |
| 6 | Ask the Booth on production | Dimebot | Backend endpoint, James's yes on API spend | Small |
| 7 | Player props research | Outlier, BettingPros | Odds API credit (James) | Medium |
| 8 | Spreads and totals | everyone | Odds API credit (James) | Medium |

Items 1–5 cost nothing new. Items 6–8 need James's yes because they spend money.

## Sources

- [Action Network PRO Report](https://www.actionnetwork.com/nfl/nfl-pro-report-sharp-action-betting-systems-model-projections-expert-picks), [public money](https://www.actionnetwork.com/nba/public-money), [Action PRO tools](https://www.actionnetwork.com/general/action-pro-picks-tools-projections), [app](https://www.actionnetwork.com/general/action-network-mobile-app-must-have-sports-fans), [RotoGrinders picks apps](https://rotogrinders.com/sports-betting/guides/best-picks-apps)
- [OddsJam review (RotoWire)](https://www.rotowire.com/news/best-betting-strategy-for-the-long-term-get-a-7-day-free-trial-for-sharp-money-97444), [screensdesign](https://screensdesign.com/showcase/oddsjam-sharp-sports-betting), [+EV finders](https://breakingac.com/news/2026/jan/07/best-positive-ev-bet-finders-free-paid-options/)
- [Unabated (The Handle)](https://thehandle.substack.com/p/unabated), [SBC Americas](https://sbcamericas.com/2024/10/21/nba-season-unabated-sports-platform/), [odds screens 2026](https://www.bettoredge.com/post/top-odds-screens-for-sports-bettors-in-2026)
- [Rithmm App Store](https://apps.apple.com/us/app/-/id1641010681), [SBC Americas](https://sbcamericas.com/2023/09/14/rithmm-ai-driven-sports-betting/)
- [Dimers Pro](https://www.ciphersports.io/press/dimers-unveils-groundbreaking-sports-betting-technology-with-dimers-pro), [Dimers app](https://apps.apple.com/us/app/dimers-picks-props-parlays/id6754231679)
- [Leans.AI](https://similarlabs.com/p/leans-ai)
- [Pikkit App Store](https://apps.apple.com/app/id1586567110), [Pikkit](https://alternativeto.net/software/pikkit/about)
- [Outlier](https://apps.apple.com/us/app/-/id6443885102), [BettingPros](https://apps.apple.com/app/id1468109182), [MVP Capping](https://www.producthunt.com/p/mvp-capping)
- Roundups: [SignalOdds](https://signalodds.com/blog/best-ai-sports-betting-tools-2026-comparison), [ReadWrite](https://readwrite.com/gambling/betting/ai-betting-prediction/), [PropsBot](https://propsbot.ai/best-ai-sports-betting-app/), [Picks & Parlays](https://picksandparlays.net/reviews/ai-picks/)
