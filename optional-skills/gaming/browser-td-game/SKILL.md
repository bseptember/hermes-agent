---
name: browser-td-game
description: "Build a browser tower-defense game from scratch (React + TS + Vite + Canvas). Engine/render/store split, data-driven maps, headless testing, polish checklist."
version: 1.0.0
author: Hermes Agent (learned from Last Stand repo)
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [game, tower-defense, browser, react, typescript, canvas, vite, architecture]
    related_skills: [plan, test-driven-development, subagent-driven-development]
    source_repo: "https://github.com/bseptember/last-stand"
---

# Browser Tower-Defense Game

## When to use

- User asks to build a browser-based tower-defense (or similar real-time Canvas) game
- User wants a "ship-ready" or "commercial-feel" browser game with React + Canvas
- User mentions tower defense, wave survival, or real-time strategy browser game
- User wants to scaffold a game with a framework-agnostic simulation core

## Architecture: the non-negotiable split

The single most important decision is the **three-layer separation**. Get this
right and everything else (testing, perf, polish) falls into place. Get it wrong
and you'll have untestable, unperformant, React-coupled spaghetti.

```
src/
  game/          PURE SIMULATION — no React, no Canvas, no DOM
    types.ts       Shared domain types (Tower, Enemy, Projectile, HudSnapshot)
    constants.ts   Data: grid, maps (MapDef), tower/enemy defs, wave table, difficulty
    engine.ts      Engine class: step(dt) advances ALL mutable state
  render/
    renderer.ts   PURE Canvas2D — draw(engine, w, h, state). Never mutates engine.
  audio/
    audio.ts      Howler wrapper: BGM + SFX pool, global mute, SFX throttle
  store/
    game.ts       Zustand: phase state machine + HUD snapshot bridge
    settings.ts   Zustand: difficulty/map/mute/reducedMotion (localStorage-persisted)
  components/     React UI (all framework-side, reads engine via store)
    GameCanvas.tsx, HUD.tsx, TowerShop.tsx, TowerInspector.tsx,
    TitleScreen.tsx, DifficultySelect.tsx, PauseMenu.tsx,
    GameOverScreen.tsx, VictoryScreen.tsx, LoadingScreen.tsx, Icons.tsx
  App.tsx         Phase state machine + keyboard shortcuts + overlay composition
```

**Why this matters:**
- `engine.step(dt)` is pure w.r.t. its injected RNG → 100% deterministic → headless-testable in Node with zero DOM.
- The renderer is a pure function of engine state → you can swap renderers, screenshot, or run headless.
- React only reads the engine via a throttled HUD snapshot (12 Hz) → no per-frame React re-renders (the perf killer).

## The engine contract

```typescript
class Engine {
  // mutable state (towers, enemies, projectiles, particles, gold, lives, wave, status)
  step(dt: number): void;          // advance simulation by dt seconds; idempotent if status !== 'playing'
  tryPlaceTower(col, row, kind): boolean;
  upgradeTower(id): boolean;
  sellTower(id): boolean;
  startNextWave(force?): void;
  reset(difficulty, mapId): void;
  snapshot(selfScore, selectedKind): HudSnapshot;
}
```

- **Fixed timestep with sub-stepping**: `requestAnimationFrame` loop computes `dt`, clamps to 0.05s max, calls `eng.step(dt)`.
- **RNG injection**: `new Engine({ rng: seededLcg })` for deterministic tests.
- **Sound as events**: engine calls `onSound('wave_start')` etc.; the audio layer subscribes. Engine never imports audio.

## Data-driven maps (so adding content is data, not code)

```typescript
interface MapDef { id: MapId; name: string; desc: string; pathTiles: Array<[number, number]>; }
const MAPS: Record<MapId, MapDef> = { classic: {...}, siege: {...} };
```

`getMapGeometry(mapId)` precomputes path points, blocked-tile set, segments, base
position, and caches it. The engine holds `private geo = getMapGeometry(mapId)`
and exposes `blocked`/`basePos`/`pathPoints` getters. The renderer reads these
getters — so adding a map is a new `MapDef` entry with zero engine/renderer changes.

## Enemy variety via flag fields (not subclasses)

```typescript
interface EnemyDef {
  // base stats: hp, speed, bounty, radius, color
  isBoss?: boolean;
  damageReduction?: number;   // armored: flat % reduction
  splitsOnDeath?: boolean;    // splitter: spawn 2 grunts at death location
  flying?: boolean;           // flyer: ignores path, flies straight to base
}
```

- **Ground enemies** follow the polyline path via `seg`/`segT`.
- **Flyers** use `flyProgress` (0→1 lerp entry→base) — bypasses path entirely,
  forces players to build coverage towers instead of stacking at path corners.
- **Splitter** death calls `spawnEnemyAt(x, y, 'grunt')` which snaps to the nearest path position.

## Wave table (data, not code)

```typescript
interface WaveDef { groups: SpawnGroup[]; reward: number; boss?: boolean; }
interface SpawnGroup { kind: EnemyKind; count: number; interval: number; delay: number; }
```

The engine flattens groups into a spawn queue at wave start and pops on a clock.
Difficulty scaling multiplies HP/speed/bounty; per-wave HP growth is a single
constant `WAVE_HP_GROWTH`.

## The two UI-state bugs you WILL hit (don't repeat)

### 1. The TOGGLE bug (tower selection)
`TowerShop.selectKind` is a TOGGLE: `selectedKind === k ? null : k`. If you
pre-select a kind in `beginRun`, the player's first shop click DESELECTS instead
of selects, and placement breaks. **Never pre-select a kind.** Clear it on restart:
`setHud({ ...hud, selectedKind: null, selectedTowerId: null })`.

### 2. The HUD-clobber bug (12 Hz snapshot)
The rAF loop pushes engine state to the store ~12×/sec for the HUD. If you use
`setHud(snapshot)`, it replaces the whole snapshot — wiping any tower the player
just clicked. Use a **merge**:
```typescript
syncEngineHud: (partial) => set((s) => ({ hud: { ...s.hud, ...partial } }))
```
Only push engine-derived fields (gold/lives/wave/score); never touch
`selectedKind`/`selectedTowerId` from the rAF loop.

## Headless testing (the payoff of the engine split)

Because `engine.step(dt)` is pure and DOM-free, you can bundle it with esbuild
and run full games in Node:

```javascript
// scripts/sim_test.mjs — bundle the engine, run to victory/defeat
import { build } from 'esbuild';
const entry = `import { Engine } from '<abs>/src/game/engine.ts'; ...`;
const res = await build({ entryPoints:[out], bundle:true, format:'esm', platform:'node', write:false });
await import(pathToFileURL(bundle).href);
```

Three test tiers:
1. **Unit tests** (`unit_test.mjs`): placement, upgrade/sell, slow, splash, waves,
   boss, difficulty, flyer movement, map switching, edge cases.
2. **Sim test** (`sim_test.mjs`): proves victory AND defeat are reachable (no
   unwinnable or unlosable states).
3. **Balance harness** (`balance_test.mjs`): plays each difficulty × N seeds
   under two strategies (coverage-ranked/fast and random/slow) to tune the
   difficulty curve. Target: easy = forgiving for any play; normal = winnable
   with minor life loss; hard = brutal-but-fair (casual loses ~60%).

## Polish checklist (ship-ready, not just "works")

- [ ] **Audio is non-fatal**: every `audio.play()` is in try/catch. A Howler
  failure must never block gameplay. Throttle high-frequency SFX (shoot) to 70ms.
- [ ] **BGM stops on victory/defeat** so the jingle plays clean.
- [ ] **prefers-reduced-motion**: actually disables screen shake AND particles
  AND muzzle flashes (not just shake — that's the common half-implementation).
- [ ] **Particle pool cap** (600 oldest-dropped-first) so mass deaths can't tank FPS.
- [ ] **Renderer perf**: cache the static background to an offscreen canvas
  (`bgCache`). Never call `createRadialGradient` per projectile per frame —
  use a cached glow sprite + `drawImage`, or layered arcs. Particles = `fillRect`
  not `arc` (3× cheaper at 2-5px).
- [ ] **Settings persist** to localStorage (`laststand_settings`).
- [ ] **Restart clears stale UI selection** (the TOGGLE bug prevention).
- [ ] **Meta-progression**: high scores per difficulty+map (localStorage), star
  rating on victory (★★★ no lives lost, ★★ ≤30% lost, ★ survived).
- [ ] **Accessibility**: aria-labels on all interactive elements, `:focus-visible`
  outlines, keyboard 1/2/3 hotkeys, colorblind-friendly enemy shapes
  (circle/triangle/square/diamond/hexagon/chevron — not just color).
- [ ] **GitHub Pages-safe**: `vite.config.ts` `base: './'`; audio paths use
  `import.meta.env.BASE_URL` so Howler loads `./audio/x.wav` not `/audio/x.wav`.

## Tech stack (proven, minimal, no backend)

- **React + TypeScript** — UI layer only
- **Vite** — build/dev (`base: './'` for GitHub Pages)
- **TailwindCSS** — styling
- **Canvas2D** — renderer (pure draw function, no React)
- **Zustand** — phase state + HUD bridge (the merge pattern above)
- **Howler.js** — audio (procedural WAVs, no network)
- **Framer Motion** — menu transitions only (never in the game loop)
- **esbuild** — bundles the engine for headless Node tests

## Verification gates (run before declaring done)

1. `npm run build` — must be 0 TypeScript errors.
2. `node scripts/unit_test.mjs` — all assertions pass.
3. `node scripts/sim_test.mjs` — both victory and defeat reachable.
4. `node scripts/balance_test.mjs` — difficulty curve matches the target above.
5. `npx tsc --noEmit` — strict typecheck clean.

## Pitfalls (don't repeat these)

- **Module-level path constants** make multi-map impossible. Resolve geometry
  per-map in the engine and expose via getters.
- **`setHud(snapshot)` in the rAF loop** clobbers UI selection. Always merge.
- **Pre-selecting a tower kind** breaks the toggle. Clear, never pre-select.
- **`createRadialGradient` per projectile per frame** is the #1 renderer hot spot.
  Cache a sprite or use layered arcs.
- **Reduced-motion that only disables shake** leaves particles/muzzle running —
  read the setting in the renderer's draw call and gate all three.
- **Audio paths without `BASE_URL`** 404 on GitHub Pages subpaths.
- **No BGM stop on victory** means the jingle fights the loop. Stop BGM on terminal states.

## Reference implementation

The "Last Stand" repo (`bseptember/last-stand`) is a complete, ship-ready
reference for this skill: 2 maps, 7 enemy types (incl. flyers), 3 towers with
upgrades, meta-progression, 70 headless unit tests, balance harness, GitHub
Pages deployment. Read its `docs/ARCHITECTURE.md` for the full module map and
data-flow diagram.
