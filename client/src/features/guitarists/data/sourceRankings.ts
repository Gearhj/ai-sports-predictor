import type { Guitarist, RankingEntry, RankingSource, Style } from "./guitarists";

type SourceRow = {
  rank: number;
  name: string;
  styles: Style[];
};

const sourceStyleMap: Array<[string, Style]> = [
  ["metal", "Metal"],
  ["classical", "Classical"],
  ["flamenco", "Classical"],
  ["brazilian", "Classical"],
  ["jazz", "Jazz"],
  ["fusion", "Jazz"],
  ["country", "Country"],
  ["bluegrass", "Country"],
  ["folk", "Country"],
  ["rockabilly", "Country"],
  ["blues", "Blues"],
  ["r&b", "Blues"],
  ["soul", "Blues"],
  ["funk", "Blues"],
  ["rock", "Rock"],
];

const canonicalNames: Record<string, string> = {
  "Agustin Barrios Mangore": "Agustín Barrios Mangoré",
  "Al DiMeola": "Al Di Meola",
  "Andres Segovia": "Andrés Segovia",
  "B.B. King": "B.B. King",
  "Billy F. Gibbons": "Billy Gibbons",
  "Edward Van Halen": "Eddie Van Halen",
  "Hank marvin": "Hank Marvin",
  "Luiz Bonfa": "Luiz Bonfá",
  "Michael Bloomfield": "Mike Bloomfield",
  "Mother Maybelle Carter": "Maybelle Carter",
  "Paco DeLucia": "Paco de Lucía",
  "Paco de Lucia": "Paco de Lucía",
};

const digitalDreamDoorRaw = `
1. Andres Segovia (classical)
2. Django Reinhardt (jazz)
3. Chet Atkins (country)
4. Jimi Hendrix (rock)
5. Paco de Lucia (flamenco)
6. Agustin Barrios Mangore (classical)
7. Ramon Montoya (flamenco)
8. Julian Bream (classical)
9. Charlie Christian (jazz)
10. B.B. King (blues)
11. T-Bone Walker (blues)
12. Merle Travis (country)
13. Wes Montgomery (jazz)
14. John Williams (classical)
15. Michael Hedges (contemporary fingerstyle)
16. Lonnie Johnson (blues)
17. Eddie Lang (jazz)
18. Lenny Breau (jazz)
19. John McLaughlin (fusion)
20. Joe Pass (jazz)
21. Sabicas (flamenco)
22. Blind Blake (ragtime, blues)
23. Robert Johnson (blues)
24. John Fahey (contemporary fingerstyle, folk)
25. Davey Graham (british folk, contemporary fingerstyle)
26. Doc Watson (folk)
27. Danny Gatton (rockabilly)
28. Adrian Legg (contemporary fingerstyle)
29. Narciso Yepes (classical)
30. Laurindo Almeida (brazilian)
31. Les Paul (jazz)
32. Christopher Parkening (classical)
33. Pat Metheny (fusion, jazz)
34. Sol Ho'opi'i (hawaiian)
35. Jeff Beck (rock)
36. Eddie Van Halen (rock)
37. Ritchie Blackmore (rock)
38. Alexandre Lagoya and Ida Presti (classical)
39. Phil Keaggy (rock, contemporary fingerstyle)
40. Allan Holdsworth (fusion)
41. Baden Powell (brazilian)
42. Nino Ricardo (flamenco)
43. George Van Eps (jazz)
44. Jim Hall (jazz)
45. Ed Bickert (jazz)
46. Kenny Burrell (jazz)
47. Franco (soukous, rumba)
48. Carlos Paredes (fado)
49. Freddie Green (jazz)
50. Eric Clapton (rock, blues)
51. Jimmy Page (rock)
52. Albert King (blues)
53. Hank Garland (country, jazz)
54. Chuck Berry (rock)
55. Tommy Emmanuel (contemporary fingerstyle)
56. Leo Kottke (contemporary fingerstyle)
57. Tony Iommi (rock)
58. King Bennie Nawahi (hawaiian)
59. Enver Izmailov (fusion)
60. Stanley Jordan (jazz, fusion)
61. Robert Fripp (avant-garde, rock)
62. Oscar Moore (jazz)
63. Ernest Ranglin (ska, jazz)
64. Gabby Pahinui (hawaiian slack key)
65. Vishwa Mohan Bhatt (indian)
66. Johnny Smith (jazz)
67. Roy Buchanan (rock)
68. Bill Frisell (fusion, jazz)
69. Manuel Barrueco (classical)
70. Kazuhito Yamashita (classical)
71. Jimmy Bryant (country, jazz)
72. Duane Allman (rock, blues)
73. James Burton (rockabilly)
74. Freddie King (blues)
75. Elmore James (blues)
76. Earl Hooker (blues)
77. Juanjo Dominguez (tango)
78. Roberto Grela (tango)
79. Mother Maybelle Carter (country)
80. Stevie Ray Vaughan (blues)
81. Steve Vai (rock)
82. Yngwie Malmsteen (rock)
83. Steve Morse (rock)
84. Eric Johnson (rock)
85. Tony Rice (bluegrass)
86. Bola Sete (brazilian, folk fusion)
87. Richard Thompson (british folk)
88. John Renbourn (british folk)
89. Bert Jansch (british folk)
90. Buddy Guy (blues)
91. Steve Cropper (r&b, blues)
92. Robert White / Joe Messina / Eddie Willis (r&b)
93. Scotty Moore (rockabilly)
94. Barney Kessel (jazz)
95. Tal Farlow (jazz)
96. Jimmy Raney (jazz)
97. Howard Roberts (jazz)
98. George Benson (jazz, r&b)
99. Debashish Bhattacharya (indian)
100. Ry Cooder (blues)
101. Roy Nichols (country)
102. Brian May (rock)
103. Carlos Santana (rock)
104. David Gilmour (rock)
105. Jan Akkerman (rock)
106. Larry Carlton (fusion)
107. Larry Coryell (fusion, jazz)
108. Al DiMeola (fusion)
109. Steve Howe (rock)
110. Shawn Lane (fusion)
111. Joe Satriani (rock)
112. Rev. Gary Davis (ragtime, blues)
113. Derek Bailey (avant-garde)
114. Sonny Sharrock (avant-garde)
115. Sonny Greenwich (jazz, avant-garde)
116. James Blood Ulmer (jazz, avant-garde)
117. Pepe Romero (classical)
118. Angel Romero (classical)
119. Carlos Montoya (flamenco)
120. Martin Taylor (jazz)
121. Alirio Diaz (classical)
122. David Russell (classical)
123. Sandy Bull (folk fusion)
124. Koo Nimo (highlife)
125. Paulinho Nogueira (brazilian)
126. Grant Green (jazz, r&b)
127. Diblo Dibala (soukous)
128. Doctor Nico (soukous)
129. Paul Galbraith (classical)
130. Mario Escudero (flamenco)
131. Joe Maphis (country)
132. Luiz Bonfa (brazilian)
133. Pat Martino (jazz)
134. Steve Hackett (rock, classical)
135. John Abercrombie (fusion, jazz)
136. Ralph Towner (fusion, jazz)
137. John Scofield (jazz, fusion)
138. Otis Rush (blues)
139. Melchor de Marchena (flamenco)
140. Brent Mason (country)
141. Oscar Aleman (jazz)
142. Goran Sollscher (classical)
143. Eliot Fisk (classical)
144. Marcel Dadi (contemporary fingerstyle)
145. Lou Harrison (classical)
146. Roland Dyens (classical)
147. Jorma Kaukonen (rock, blues)
148. Peter Lang (contemporary fingerstyle)
149. Davy Graham (british folk)
150. Pierre Bensusan (contemporary fingerstyle)
151. Leo Brouwer (classical)
152. Paul Simon (folk, rock)
153. Yomo Toro (latin)
154. Luiz Bonfá (brazilian)
155. John Martyn (folk)
156. Stefan Grossman (blues, folk)
157. Earl Klugh (jazz)
158. Mark Knopfler (rock)
159. Johnny Guitar Watson (blues)
160. Carlos Barbosa-Lima (brazilian, classical)
161. Charlie Byrd (jazz, brazilian)
162. Lonnie Mack (rock, blues)
163. Dick Dale (rock)
164. Link Wray (rock)
165. EK Nyame (highlife)
166. Big Bill Broonzy (blues)
167. Roy Lanham (country)
168. Scotty Anderson (jazz, country)
169. Jimmy Nolen (funk)
170. Clarence White (country)
171. Jerry Reed (country)
172. Roy Clark (country)
173. Grady Martin (country)
174. Albert Collins (blues)
175. George Harrison (rock)
176. Keith Richards (rock)
177. Eldon Shamblin (western swing)
178. Johnny Winter (blues)
179. Jerry Garcia (rock)
180. Henry Kaiser (avant-garde)
181. Eugene Chadbourne (avant-garde)
182. Alex Konadu (highlife)
183. Marty Friedman (rock)
184. Uli Jon Roth (rock)
185. Michael Schenker (rock)
186. Preston Reed (contemporary fingerstyle)
187. Herb Ellis (jazz)
188. Mike Stern (jazz)
189. Junior Brown (country)
190. Terje Rypdal (fusion)
191. Blind Lemon Jefferson (blues)
192. Alvin Lee (rock)
193. Peter Green (blues, rock)
194. Eddie Hazel (rock)
195. Vinnie Moore (rock)
196. Fred Frith (avant-garde)
197. Vicente Amigo (flamenco)
198. Martin Simpson (celtic)
199. Tommy Tedesco (session)
200. Blind Willie Johnson (blues)
`;

const vintageGuitarRaw = `
1. Jimi Hendrix
2. Eric Clapton
3. Jimmy Page
4. Jeff Beck
5. Stevie Ray Vaughan
6. B.B. King
7. Chet Atkins
8. David Gilmour
9. George Harrison
10. Django Reinhardt
11. Edward Van Halen
12. Joe Bonamassa
13. Duane Allman
14. Mark Knopfler
15. Keith Richards
16. Gary Moore
17. Les Paul
18. Carlos Santana
19. Joe Satriani
20. Billy F. Gibbons
21. Rory Gallagher
22. Steve Vai
23. Slash
24. Peter Green
25. Tommy Emmanuel
26. Wes Montgomery
27. Ritchie Blackmore
28. Joe Pass
29. Brian May
30. Danny Gatton
31. Johnny Winter
32. Buddy Guy
33. Chuck Berry
34. Eric Johnson
35. Scotty Moore
36. Steve Howe
37. James Burton
38. Roy Buchanan
39. Tony Iommi
40. Derek Trucks
41. John Frusciante
42. John McLaughlin
43. Michael Bloomfield
44. Frank Zappa
45. Brian Setzer
46. Larry Carlton
47. Randy Rhoads
48. Robben Ford
49. Prince
50. Ry Cooder
51. Warren Haynes
52. Robert Johnson
53. Andrés Segovia
54. Link Wray
55. Freddie King
56. Joe Walsh
57. Angus Young
58. Paco DeLucia
59. Pat Metheny
60. Pete Townshend
61. Alex Lifeson
62. Albert King
63. Albert Lee
64. George Benson
65. Richard Thompson
66. Steve Morse
67. The Edge
68. Tony Rice
69. Jerry Garcia
70. Alvin Lee
71. Jack White
72. Mike Campbell
73. Robin Trower
74. Neil Young
75. Doc Watson
76. Brad Paisley
77. Kenny Burrell
78. Robert Fripp
79. Charlie Christian
80. Jerry Reed
81. Vince Gill
82. Yngwie Malmsteen
83. Steve Cropper
84. Al DiMeola
85. Adrian Belew
86. Lindsey Buckingham
87. John Mayer
88. Paul Gilbert
89. John Petrucci
90. Nels Cline
91. Johnny Marr
92. Guthrie Govan
93. Barney Kessel
94. T-Bone Walker
95. Hank marvin
96. Duane Eddy
97. Kirk Hammett
98. Leslie West
99. Stephen Stills
100. Dimebag Darrell
`;

export const sourceRankingSources: RankingSource[] = [
  {
    id: "vintage-guitar-2016",
    name: "Top 100 Guitarists",
    publisher: "Vintage Guitar",
    year: 2016,
    type: "Reader poll",
    authority: 82,
    url: "https://www.vintageguitar.com/25972/top-30-guitarists/",
  },
  {
    id: "digital-dream-door-2008",
    name: "200 Greatest Guitarists (All Genres)",
    publisher: "DigitalDreamDoor",
    year: 2008,
    type: "Editor list",
    authority: 64,
    url: "https://digitaldreamdoor.com/pages/best_guitar-all.html",
  },
  {
    id: "guitar-masterclass-forum",
    name: "Top 100 Guitarists",
    publisher: "GuitarMasterClass forum",
    year: 2010,
    type: "Genre list",
    authority: 40,
    url: "https://www.guitarmasterclass.net/guitar_forum/index.php?showtopic=14718",
  },
];

function canonicalizeName(name: string) {
  const clean = name
    .replace(/\bCGP\b/g, "")
    .replace(/\*/g, "")
    .replace(/\s+/g, " ")
    .trim();
  return canonicalNames[clean] ?? clean;
}

function idFromName(name: string) {
  return `rank-${name
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")}`;
}

function normalizedName(name: string) {
  return name
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase();
}

function inferStyles(styleText: string): Style[] {
  const lowered = styleText.toLowerCase();
  const styles = sourceStyleMap.filter(([keyword]) => lowered.includes(keyword)).map(([, style]) => style);
  const unique = Array.from(new Set(styles));
  return unique.length ? unique : ["Rock"];
}

function parseRows(raw: string, fallbackStyles: Style[]) {
  const rows: SourceRow[] = [];
  for (const line of raw.trim().split("\n")) {
    const match = line.trim().match(/^(\d+)\.\s+(.+?)(?:\s+\((.+)\))?$/);
    if (!match) continue;
    const rank = Number(match[1]);
    const namePart = match[2].trim();
    const stylePart = match[3] ?? "";
    const names = namePart.split(/\s+\/\s+|\s+ and \s+/i).map(canonicalizeName);
    for (const name of names) {
      rows.push({
        rank,
        name,
        styles: stylePart ? inferStyles(stylePart) : fallbackStyles,
      });
    }
  }
  return rows;
}

const sourceRowsBySource: Record<string, SourceRow[]> = {
  "digital-dream-door-2008": parseRows(digitalDreamDoorRaw, ["Rock"]),
  "vintage-guitar-2016": parseRows(vintageGuitarRaw, ["Rock"]),
};

function dedupeRows(rows: SourceRow[]) {
  const best = new Map<string, SourceRow>();
  for (const row of rows) {
    const key = normalizedName(row.name);
    const current = best.get(key);
    if (!current || row.rank < current.rank) {
      best.set(key, row);
    }
  }
  return Array.from(best.values());
}

const sourceRows = Object.values(sourceRowsBySource).flatMap(dedupeRows);

function scoreFromRank(rank: number, sourceSize: number) {
  return Math.max(28, Math.round(86 - (rank / sourceSize) * 34));
}

export const sourceRankingGuitarists: Guitarist[] = Array.from(
  new Map(
    sourceRows.map((row) => [
      normalizedName(row.name),
      {
        id: idFromName(row.name),
        name: row.name,
        styles: row.styles,
        primaryStyle: row.styles[0],
        country: "Unknown",
        activeFrom: 1970,
        activeTo: 2026,
        bands: ["Source ranking import"],
        pollScore: scoreFromRank(row.rank, 200),
        influence: scoreFromRank(row.rank, 200),
        technique: row.styles.includes("Jazz") || row.styles.includes("Classical") || row.styles.includes("Metal") ? 72 : 64,
        innovation: scoreFromRank(row.rank, 200) - 5,
        commercial: 55,
        longevity: 70,
        signature: "Imported from published guitarist ranking sources. Needs curated bio metadata.",
      } satisfies Guitarist,
    ]),
  ).values(),
);

export const sourceRankingEntries: RankingEntry[] = Object.entries(sourceRowsBySource).flatMap(([sourceId, rows]) =>
  dedupeRows(rows).map((row) => ({
    sourceId,
    guitaristId: idFromName(row.name),
    guitaristName: row.name,
    rank: row.rank,
  })),
);
