/**
 * Terms of use, privacy and the age statement (scale plan Phase 1, item 4). DRAFTS written by Claude for the
 * owner, marked as such on the page until a lawyer has read them (Phase 2). Plain words on purpose: the three
 * things every reader must leave with are that this is information and entertainment, that nothing here places
 * a wager or pays a prize, and that the prices shown are other people's and may be late.
 */
import { Link } from 'react-router-dom'
import { Scale, ShieldCheck } from 'lucide-react'
import { PageHeader, Panel } from '../../components/ui'

export const LEGAL_UPDATED = '2 November 2026'
const DRAFT = 'DRAFT · written 2 November 2026 · not yet reviewed by a lawyer'

function Draft() {
  return <p className="legal-draft" role="note"><ShieldCheck size={13} /> {DRAFT}. Until that review, the plain-language meaning stands: no wagering, no prizes, no advice.</p>
}

export function TermsPage() {
  return (
    <div className="page legal-page">
      <PageHeader eyebrow="TERMS OF USE" title="What AI Sports Predictor is, and is not" description={`Last updated ${LEGAL_UPDATED}. Read with the privacy policy.`} />
      <Draft />
      <Panel title="1. What this site is" eyebrow="INFORMATION AND ENTERTAINMENT">
        <p>AI Sports Predictor ("AISP", "the site", "we") publishes the output of a forecasting system: a probability for each game, how that compares with the prices sportsbooks are showing, a running public record of every call the system made, a live second-screen view of games, and games you can play against the system for bragging rights. It is an information and entertainment service.</p>
        <p><b>It is not a gambling service.</b> Nothing on this site places, accepts, brokers or settles a wager of any kind. The "paper", "virtual" and "play money" figures are a public scorecard of the system's own calls and of your own practice picks; they have no cash value, cannot be withdrawn, and are not a promise of any outcome. No competition, arena, league or game on this site pays a prize of money or anything of value.</p>
        <p><b>It is not advice.</b> A probability is a measurement of uncertainty, not a prediction of what will happen, and a "value" against a sportsbook's price is a statement about that price, not a recommendation to act. If you choose to wager anywhere, that is your decision, under the laws where you are, and at your own risk. We take no part in it.</p>
      </Panel>
      <Panel title="2. Who may use it" eyebrow="AGE AND PLACE">
        <p>You must be at least 18 years old to create an account. By creating one you confirm that you are, and that using an information service about sports and their betting markets is lawful where you live. Where the law sets a higher age for anything to do with betting markets, that higher age applies to you.</p>
        <p>During the beta, accounts are created by invitation and the site may be unavailable, changed or reset at any time without notice.</p>
      </Panel>
      <Panel title="3. The prices and data you see" eyebrow="THIRD-PARTY, MAY BE LATE">
        <p>Sportsbook prices, consensus lines, scores, play-by-play and injury news come from third parties. They are shown as received, may be delayed, incomplete or wrong, and may not be the price that any sportsbook would give you. We do not guarantee them and you must not rely on them for anything that matters. The sportsbooks named are not connected with this site.</p>
        <p>The system's own numbers are published with their history so you can judge them; past performance, good or bad, says nothing about the next game.</p>
      </Panel>
      <Panel title="4. Your account and your conduct" eyebrow="ACCOUNTS">
        <p>Keep your password to yourself; what is done with your account is done by you until you tell us otherwise. You may close your account at any time from the Account page, which removes the picks, calls, progress and model saved to it.</p>
        <p>Do not scrape, mirror, resell or redistribute the data on this site, interfere with its operation, or use it to break the law. We may suspend or close an account that does.</p>
      </Panel>
      <Panel title="5. Membership" eyebrow="WHEN A PAID TIER EXISTS">
        <p>A paid membership, when offered, buys access to research features for the period paid, nothing else: no wager, no stake, no prize, no share of any outcome. Prices and what the membership includes are stated at the point of purchase; billing is handled by a payment provider under its own terms. You can cancel for the next period at any time.</p>
      </Panel>
      <Panel title="6. Liability and changes" eyebrow="THE SMALL PRINT">
        <p>The site is provided as it is and as it is available. To the fullest extent the law allows, we are not liable for any loss arising from the use of, or inability to use, the site or anything shown on it, including any wager anyone places anywhere. Where liability cannot be excluded it is limited to what you paid us in the twelve months before the claim.</p>
        <p>We may change these terms; the date at the top moves when we do, and continuing to use the site after that is acceptance. Questions go through the feedback link on the site.</p>
      </Panel>
      <p className="legal-foot"><Scale size={13} /> See also the <Link to="/privacy">privacy policy</Link>.</p>
    </div>
  )
}

export function PrivacyPage() {
  return (
    <div className="page legal-page">
      <PageHeader eyebrow="PRIVACY" title="What we keep about you, and why" description={`Last updated ${LEGAL_UPDATED}. Read with the terms of use.`} />
      <Draft />
      <Panel title="1. What we collect" eyebrow="THE LIST">
        <ul className="legal-list">
          <li><b>Account:</b> your email address, a display name you choose, and a one-way hash of your password (we cannot read the password itself). Whether and when you confirmed the email.</li>
          <li><b>Your play:</b> the practice picks, Arena calls, "you call it" snaps, site progress (visits, streaks, badges) and model-builder settings you make while signed in, saved to your account so they follow you to another device. Signed out, they stay in your browser only.</li>
          <li><b>Operations:</b> server logs with the IP address, time and path of each request, kept for a short period to keep the site up and to limit abuse. Sign-in attempts are counted per account and per address for the same reason.</li>
          <li><b>In your browser:</b> a session token that expires, and local copies of the play above and of your display preferences. No advertising cookies, no third-party trackers, no analytics scripts that follow you across sites.</li>
        </ul>
      </Panel>
      <Panel title="2. What we do with it" eyebrow="PURPOSES">
        <p>To run your account, show you your own play, keep the site safe, and send you the few emails an account needs: confirming your address, resetting a password, and, if you ask for them, alerts. We do not sell personal data, and we do not use it to profile you for advertising.</p>
        <p>A paid membership, when offered, is billed by a payment provider; it sees the card details, we see only that the membership is active. The site is served through Cloudflare, which sees the traffic as any network carrier would.</p>
      </Panel>
      <Panel title="3. How long, and how to leave" eyebrow="RETENTION AND DELETION">
        <p>Account data stays while the account exists. Deleting your account from the Account page removes your play, your entitlements and your personal details at once; the account record itself is anonymised rather than erased, because other records reference it. Server logs expire on their own schedule. Backups are encrypted and roll off within weeks.</p>
        <p>You can see what the account holds (your play) on the pages that show it, change your display name and password on the Account page, and ask a question or ask for a copy of your data through the feedback link on the site.</p>
      </Panel>
      <Panel title="4. Children" eyebrow="AGE">
        <p>The site is for adults. We do not knowingly keep an account for anyone under 18; if we learn of one, we close it.</p>
      </Panel>
      <Panel title="5. Changes" eyebrow="THIS DOCUMENT">
        <p>The date at the top moves when this policy changes. Significant changes are announced on the site.</p>
      </Panel>
      <p className="legal-foot"><Scale size={13} /> See also the <Link to="/terms">terms of use</Link>.</p>
    </div>
  )
}
