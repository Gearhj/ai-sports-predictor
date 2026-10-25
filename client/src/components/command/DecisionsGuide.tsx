/**
 * How to read Daily Decisions (James 10-25: "I don't know how to work it"). Three plain sentences and a colour
 * key that matches every status on the cards below, so a waiting or blocked game reads as a problem at a glance.
 */
const KEY: Array<[string, string, string]> = [
  ['blue', 'Scheduled / deciding now', 'Forecast and prices are in. AISP decides on its own in the window.'],
  ['orange', 'Waiting on data', 'The model forecast or a sportsbook price is missing. Nothing can be bet until it arrives.'],
  ['red', 'Blocked', 'The window is open and something is still missing, or the game started without a decision.'],
  ['gold', 'Early read', 'A morning look that can still change. Not a decision.'],
  ['green', 'Bet', 'AISP took a paper position.'],
  ['gray', 'Passed', 'AISP looked and the price was not good enough. A pass is a decision, not a fault.'],
]

export function DecisionsGuide() {
  return (
    <section className="dg" aria-label="How to read Daily Decisions">
      <div className="dg__steps">
        <h2>How this page works</h2>
        <ol>
          <li><b>One decision per game, made by AISP.</b> Each game is decided automatically in its window, 30 to 15 minutes before the start. You do not have to do anything here.</li>
          <li><b>The colour says where it stands.</b> Orange or red means something is missing and no position can be taken yet.</li>
          <li><b>Your own picks live on My Picks.</b> This page is AISP&apos;s log: what it bet, what it passed, and why.</li>
        </ol>
      </div>
      <ul className="dg__key" aria-label="Colour key">
        {KEY.map(([tone, name, text]) => (
          <li key={tone} className={`dg__k dg__k--${tone}`}><i aria-hidden="true" /><span><b>{name}</b><small>{text}</small></span></li>
        ))}
      </ul>
    </section>
  )
}
