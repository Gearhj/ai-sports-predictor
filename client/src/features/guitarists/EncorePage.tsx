/**
 * /encore -- the Greatest-Guitarist Methodology Lab, ported unchanged in
 * behaviour from projects/Greatest-Guitarist/guitarist-app/src/GuitaristApp.tsx.
 *
 * Changes from the source, and only these: it is a lazily loaded AISP route
 * rather than a standalone root; the hero image is imported as a module so the
 * AISP build fingerprints it; everything renders inside one CSS namespace;
 * and a discreet link returns to Methodology.
 */
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft, Database, Filter, Guitar, Search, SlidersHorizontal, Trophy } from "lucide-react";
import { type Guitarist, type RankingEntry, type Style, guitarists, rankingEntries, rankingSources, styles } from "./data/guitarists";
import fretboard from "./assets/guitar-fretboard.png";
import "./encore.css";

type MetricKey = "pollScore" | "influence" | "technique" | "innovation" | "commercial" | "longevity";
type Weights = Record<MetricKey, number>;
type ScoredGuitarist = Guitarist & {
  score: number;
  sourceEntries: Array<RankingEntry & { sourceName: string; authority: number; publisher: string; year: number }>;
};

const metricLabels: Record<MetricKey, string> = {
  pollScore: "Poll consensus",
  influence: "Influence",
  technique: "Technique",
  innovation: "Innovation",
  commercial: "Commercial impact",
  longevity: "Career length",
};

const presets = [
  {
    name: "Critics' Consensus",
    description: "Rewards high placement in major lists and long-term influence.",
    weights: { pollScore: 42, influence: 24, technique: 10, innovation: 12, commercial: 6, longevity: 6 },
  },
  {
    name: "Technical Mastery",
    description: "Prioritizes command of the instrument, range, speed, articulation, and control.",
    weights: { pollScore: 12, influence: 14, technique: 42, innovation: 18, commercial: 4, longevity: 10 },
  },
  {
    name: "Innovation",
    description: "Favors players who changed tone, technique, vocabulary, or the role of guitar.",
    weights: { pollScore: 14, influence: 24, technique: 14, innovation: 38, commercial: 2, longevity: 8 },
  },
  {
    name: "Commercial Legacy",
    description: "Balances greatness with mass reach, catalog scale, and career durability.",
    weights: { pollScore: 16, influence: 18, technique: 8, innovation: 8, commercial: 30, longevity: 20 },
  },
  {
    name: "Balanced",
    description: "Keeps every category meaningful without letting one value dominate.",
    weights: { pollScore: 24, influence: 22, technique: 18, innovation: 16, commercial: 10, longevity: 10 },
  },
] satisfies Array<{ name: string; description: string; weights: Weights }>;

const allStyles = "All styles";

function normalizedName(name: string) {
  return name
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase();
}

function normalizeWeights(weights: Weights) {
  const total = Object.values(weights).reduce((sum, value) => sum + value, 0) || 1;
  return Object.fromEntries(Object.entries(weights).map(([key, value]) => [key, value / total])) as Record<MetricKey, number>;
}

function scoreGuitarist(guitarist: Guitarist, weights: Weights) {
  const normalized = normalizeWeights(weights);
  return (Object.keys(normalized) as MetricKey[]).reduce((sum, key) => sum + guitarist[key] * normalized[key], 0);
}

function getSourceEntries(guitaristId: string, guitaristName: string) {
  const nameKey = normalizedName(guitaristName);
  return rankingEntries
    .filter((entry) => entry.guitaristId === guitaristId || (entry.guitaristName && normalizedName(entry.guitaristName) === nameKey))
    .map((entry) => {
      const source = rankingSources.find((item) => item.id === entry.sourceId);
      return {
        ...entry,
        sourceName: source?.name ?? "Unknown source",
        authority: source?.authority ?? 0,
        publisher: source?.publisher ?? "Unknown",
        year: source?.year ?? 0,
      };
    })
    .sort((a, b) => b.authority - a.authority || a.rank - b.rank);
}

function formatCareer(guitarist: Guitarist) {
  return `${guitarist.activeFrom}-${guitarist.activeTo >= 2026 ? "present" : guitarist.activeTo}`;
}

function MetricBar({ label, value }: { label: string; value: number }) {
  return (
    <div className="metric-bar">
      <div className="metric-bar__label">
        <span>{label}</span>
        <strong>{Math.round(value)}</strong>
      </div>
      <div className="metric-bar__track">
        <div className="metric-bar__fill" style={{ width: `${Math.max(0, Math.min(100, value))}%` }} />
      </div>
    </div>
  );
}

export function EncorePage() {
  useEffect(() => {
    const previous = document.title;
    document.title = "Encore";
    return () => { document.title = previous; };
  }, []);
  const [presetName, setPresetName] = useState(presets[4].name);
  const [weights, setWeights] = useState<Weights>(presets[4].weights);
  const [styleFilter, setStyleFilter] = useState<Style | typeof allStyles>(allStyles);
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState("van-halen");

  const scored = useMemo<ScoredGuitarist[]>(() => {
    const search = query.trim().toLowerCase();
    return guitarists
      .filter((guitarist) => styleFilter === allStyles || guitarist.styles.includes(styleFilter))
      .filter((guitarist) => {
        if (!search) return true;
        return [guitarist.name, guitarist.country, guitarist.primaryStyle, ...guitarist.bands].join(" ").toLowerCase().includes(search);
      })
      .map((guitarist) => ({
        ...guitarist,
        score: scoreGuitarist(guitarist, weights),
        sourceEntries: getSourceEntries(guitarist.id, guitarist.name),
      }))
      .sort((a, b) => b.score - a.score || b.pollScore - a.pollScore || a.name.localeCompare(b.name));
  }, [query, styleFilter, weights]);

  const selected = scored.find((guitarist) => guitarist.id === selectedId) ?? scored[0];

  function choosePreset(preset: (typeof presets)[number]) {
    setPresetName(preset.name);
    setWeights(preset.weights);
  }

  function changeWeight(metric: MetricKey, value: number) {
    setPresetName("Custom");
    setWeights((current) => ({ ...current, [metric]: value }));
  }

  return (
    <div className="gg-encore">
    <main className="g-app">
      <section className="g-hero">
        <img src={fretboard} alt="" />
        <div className="g-hero__shade" />
        <Link className="g-return" to="/methodology"><ArrowLeft size={15} aria-hidden /> Return to Methodology</Link>
        <div className="g-hero__content">
          <div className="g-eyebrow">
            <Guitar size={18} />
            Greatest Guitarist Methodology Lab
          </div>
          <h1>Choose the method. Watch the ranking change.</h1>
          <p>Compare guitarists across poll consensus, influence, technical command, innovation, commercial impact, and career length.</p>
        </div>
      </section>

      <section className="g-summary">
        <div>
          <Trophy size={19} />
          <span>Current leader</span>
          <strong>{scored[0]?.name ?? "No match"}</strong>
        </div>
        <div>
          <Database size={19} />
          <span>Visible players</span>
          <strong>{scored.length}</strong>
        </div>
        <div>
          <Database size={19} />
          <span>Ranking sources</span>
          <strong>{rankingSources.length}</strong>
        </div>
        <div>
          <SlidersHorizontal size={19} />
          <span>Weight total</span>
          <strong>{Object.values(weights).reduce((sum, value) => sum + value, 0)}</strong>
        </div>
      </section>

      <section className="g-workspace">
        <aside className="g-panel g-method">
          <div className="g-panel-title">
            <SlidersHorizontal size={19} />
            <h2>Methodology</h2>
          </div>
          <div className="g-presets">
            {presets.map((preset) => (
              <button key={preset.name} className={presetName === preset.name ? "active" : ""} onClick={() => choosePreset(preset)}>
                <strong>{preset.name}</strong>
                <span>{preset.description}</span>
              </button>
            ))}
          </div>
          <div className="g-weights">
            {(Object.keys(metricLabels) as MetricKey[]).map((metric) => (
              <label key={metric}>
                <span>
                  {metricLabels[metric]}
                  <strong>{weights[metric]}</strong>
                </span>
                <input type="range" min="0" max="60" value={weights[metric]} onChange={(event) => changeWeight(metric, Number(event.target.value))} />
              </label>
            ))}
          </div>
        </aside>

        <section className="g-panel g-results">
          <div className="g-toolbar">
            <label>
              <Search size={17} />
              <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search by guitarist, band, style, or country" />
            </label>
            <label>
              <Filter size={17} />
              <select value={styleFilter} onChange={(event) => setStyleFilter(event.target.value as Style | typeof allStyles)}>
                <option>{allStyles}</option>
                {styles.map((style) => (
                  <option key={style}>{style}</option>
                ))}
              </select>
            </label>
          </div>
          <div className="g-list">
            {scored.map((guitarist, index) => (
              <button key={guitarist.id} className={selected?.id === guitarist.id ? "selected" : ""} onClick={() => setSelectedId(guitarist.id)}>
                <span className="rank">{index + 1}</span>
                <span className="identity">
                  <strong>{guitarist.name}</strong>
                  <small>{guitarist.primaryStyle} · {guitarist.country}</small>
                </span>
                <span className="badges">
                  {guitarist.styles.map((style) => (
                    <em key={style}>{style}</em>
                  ))}
                </span>
                <strong className="score">{guitarist.score.toFixed(1)}</strong>
              </button>
            ))}
          </div>
        </section>

        {selected && (
          <aside className="g-panel g-detail">
            <div className="g-detail__top">
              <span>Selected guitarist</span>
              <strong>{selected.score.toFixed(1)}</strong>
            </div>
            <h2>{selected.name}</h2>
            <p>{selected.signature}</p>
            <div className="g-facts">
              <div>
                <span>Primary style</span>
                <strong>{selected.primaryStyle}</strong>
              </div>
              <div>
                <span>Career</span>
                <strong>{formatCareer(selected)}</strong>
              </div>
              <div>
                <span>Country</span>
                <strong>{selected.country}</strong>
              </div>
              <div>
                <span>Associated with</span>
                <strong>{selected.bands.slice(0, 2).join(", ")}</strong>
              </div>
            </div>
            <div className="g-metrics">
              {(Object.keys(metricLabels) as MetricKey[]).map((metric) => (
                <MetricBar key={metric} label={metricLabels[metric]} value={selected[metric]} />
              ))}
            </div>
            <div className="g-evidence">
              <h3>Poll Evidence</h3>
              {selected.sourceEntries.length ? (
                selected.sourceEntries.map((entry) => (
                  <div key={`${entry.sourceId}-${entry.rank}-${entry.guitaristName ?? selected.name}`}>
                    <span>#{entry.rank}</span>
                    <strong>{entry.publisher}</strong>
                    <small>{entry.year} · authority {entry.authority}</small>
                  </div>
                ))
              ) : (
                <p>No seeded ranking entry yet.</p>
              )}
            </div>
          </aside>
        )}
      </section>
    </main>
    </div>
  );
}

export default EncorePage;
