# Directive Fleet AI for Star Trek: Bridge Commander

AI and Quick Battle patches from **Directive Fleet**, a custom Quick Battle race for
*Star Trek: Bridge Commander* (2002) running on the **Orion Delta** mod. This repo holds
the scripts only: a fork of the stock `MultiAttack` compound AI called `DirectiveAttack`,
fixes to how AI-flown carriers launch their fighter wings, and a few smaller Quick Battle
changes. There are also two offline helper tools.

> **Unofficial fan work.** It is not affiliated with or endorsed by CBS Studios /
> Paramount, Activision or Totally Games. *Star Trek* and related marks belong to their
> owners. **No game files are included**: no models, textures, sounds or stock scripts.
> You need your own copy of the game and the mods listed below.

---

## What's in the repo

| Path | What it is | Whose code |
|---|---|---|
| `patches/DirectiveAttack.py.patch` | Unified diff that turns a copy of stock `AI/Compound/MultiAttack.py` into `DirectiveAttack.py` | 6 changed values + header by this project. The rest is Totally Games' |
| `patches/QuickBattle.py.patch` | Unified diff against Orion Delta RC4.1's `scripts/QuickBattle/QuickBattle.py` | ~360 changed lines by this project, on top of Totally Games / Orion Delta code |
| `patches/BasicAttack.py.patch` | Optional. Lets an attack AI fly a cloaked hull itself (`HANDLES_CLOAK`) | 15 lines by this project, on top of Totally Games code |
| `tools/check152.py` | Python 3 linter for Bridge Commander's Python 1.5.2 rules | Original |
| `tools/extract_ships.py` | Python 3: reads every hardpoint in an install and dumps `ships.json` (hull, shields, weapons, AIString, cloak, carrier wing) | Original |

The repo ships patches, not patched files. Stock scripts stay with their owners, and you
apply the patches to the copies in your own install.

---

## How Bridge Commander picks an attack brain

You need this to get anything out of the patches, because the obvious lever is often not
the real one.

```
Bedrock plugin  (scripts/Custom/Bedrock/Ships/<Ship>.py)
   EAI = "QuickBattleAI"  /  FAI = "QuickBattleFriendlyAI"     <- picks a WRAPPER only
        |
QuickBattle.GenerateShips -> pShip.SetAI(<wrapper>.CreateAI(pShip))
        |
QuickBattleAI.CreateAI -> AI.Compound.BasicAttack.CreateAI(pShip, <enemies>,
                                 Difficulty=<slider>, UseCloaking=1, ...)
        |
BasicAttack  = a dispatcher, not a behaviour:
   1. expands Difficulty into ~20 on/off flags (SmartPhasers only at 1.0)
   2. if UseCloaking and the hull has a cloak  -> CloakAttackWrapper
      else                                     -> the hull's AIString
```

- **The brain comes from `SetAIString()` in the HARDPOINT** (`scripts/ships/Hardpoints/<X>.py`),
  never from the Bedrock plugin. `BasicAttack` does a bare `__import__(<AIString>)`, so
  the module name is the whole contract. Add `AI/Compound/DirectiveAttack.py` and put
  `SetAIString("DirectiveAttack")` in a hardpoint, and no stock file is touched.
- **A cloak overrides the AIString.** Quick Battle always passes `UseCloaking = 1`, so
  every hull with a cloaking subsystem runs `CloakAttackWrapper` → `CloakAttack`. Its
  own AIString only runs as the fallback once the cloak has been shot off. If you tune
  the AIString of a cloaking ship, you are only tuning how it behaves once damaged. To
  make a custom AI live, either fit no cloak or apply `BasicAttack.py.patch` and have
  your AI handle the cloak (see below).
- **Nothing in the stock compound AI reads a weapon's range, charge or recharge.** Its
  weapon awareness is four yes/no conditions (torpedoes ready, pulse ready, weapon in use,
  target in phaser arc). The range bands are hard-coded:
  - `FedAttack`: 50/100/150/200/350, with one-sided jitter, so every band sits *above*
    its nominal value. Its `fLongRange` is computed and never used, because 350.0 is
    hard-coded.
  - `NonFedAttack`: 100/200/350 with no "too close" band, which is why it brawls.
  - `MultiAttack`: a flat 500.

  A hull with 60-unit beams flies exactly the same bands as one with 500-unit pulses.
- **`MultiAttack` is really "a starbase that can chase".** Its weapons brain *is*
  `StarbaseAttack`'s turret script, bolted onto some movement blocks. It has no range
  banding and no torpedo runs.

---

## `DirectiveAttack`: what changed from stock `MultiAttack`

We found these by watching a mirror match of a slow, heavily armed hull (a mobile
Deep Space Nine with 36 beams at 250 range and 24 pulses at 500):

| Stock fault | Why | Fix |
|---|---|---|
| Parks at 500 and never closes to its own beam range | `ConditionInRange(500)` + `SetInterceptDistance(500)` | Both set to **220**, just inside 250-range beams |
| Freezes under fire | The whole movement chain sits in a `SequenceAI` with `SetResetIfInterrupted(1)`. Incoming fire keeps throwing it back to the first child (gunnery), so it never reaches the movement slice | Both sequences set to **`SetResetIfInterrupted(0)`** |
| Stops firing to move, and only moves 20% of the time | Gunnery and movement are serial children of one sequence on a **4 s / 1 s** split | **2 s / 2 s** |

The structural tell: `FedAttack` has 8 `PriorityListAI`s and 1 sequence, while
`MultiAttack` has 0 and 2. A priority list re-evaluates from the top every tick and
shrugs off interruptions. A reset-on-interrupt sequence does not.

The reset fix showed up clearly in testing. On stock `MultiAttack`, the side that lost
the first exchange could never recover: its opponent's shields *regenerated* while it sat
pinned at 9%. With the fix, the losing ship fights back and wears the leader down.

The proper structural fix would fire from a `PreprocessingAI` wrapped *around* the
movement, the way `FedAttack` does. That has **not** been attempted, because it's a
rewrite rather than a tune. If 2 s / 2 s isn't enough, that's the next step.

**Tune 220 to your hull.** It suits beams that reach about 250. Bridge Commander measures
weapon range **centre to centre**, so against a physically huge target (a starbase model)
a short-ranged gun can sit alongside the hull and still be "out of range".

**Check reach before blaming the doctrine.** More than once the AI looked broken when the
real problem was that a slow ship couldn't physically reach the fight before the round
ended. Compare the spawn separation, its speed and its weapon ranges first.

---

## `QuickBattle.py.patch`: what it does

All of it lives in `scripts/QuickBattle/QuickBattle.py`.

### AI-carrier fighter wings

Quick Battle has two separate launch paths, and they are easy to confuse:

| Who flies the carrier | What launches the wing |
|---|---|
| The player | `CheckFighters` builds *Launch &lt;type&gt; (n)* buttons on the XO menu, and `SpawnFighter1..8` do the work |
| The Quick Battle AI (friendly or enemy) | `LaunchAIWing` → `SpawnAIFighter`, called from `GenerateShips` straight after the hull is registered. Neutrals are skipped |

A ship that a script spawns with `loadspacehelper.CreateShip` has neither path. It has no
bridge and no XO menu, so it must launch its own wing.

The patch fixes three real defects in the AI path:

1. **Silent failures.** Eight copy-pasted `try: … except: pass` blocks were meant to guard
   against a missing `FighterTypeN` slot, but they also wrapped the spawn call. So a real
   failure (a missing ship script, a bad plugin, a full set) was as silent as "this hull
   carries no fighters". `LaunchAIWing()` still says nothing about absent slots, but a
   failure now prints `QB wing: <ship> slot N (<type> xN) FAILED: <error>` to the console.
2. **Name collisions.** Fighter names used a random number drawn once per slot, so two
   carriers of one class could create identically named ships, and object groups are keyed
   by name. Names now use a monotonic `g_iFighterSerial`.
3. **Invisible enemy fighters.** AI-launched fighters were never registered in `g_kShips`,
   so the win test couldn't see an enemy carrier's screen. You could kill the carrier and
   the sim declared victory while six fighters were still shooting. Fighters are now
   registered and counted in `g_iNumEnemies` with an empty destroyed message.
   `ShipDestroyed` skips the voice line for an empty message, so a dying screen doesn't
   queue six announcements. `ClearFighters` deregisters before `SetDeleteMe`, so teardown
   can't trip a phantom win.

The patch also reads three optional Bedrock plugin fields:

```python
FighterType1 = "Valkyrie"       # stock fields: up to 8 slots
FighterQuantity1 = 3            # (stock carriers cap themselves at 3 per slot)
AIFighterCap = 3                # NEW: caps each slot when the AI flies the carrier;
                                #      the player's own complement is not capped
FighterLaunchPoints = ((5.8, 0, 0), (-5.8, 0, 0))
                                # NEW: one (x, y, z) per slot in the carrier's own frame
                                #      (x right, y forward, z up, hardpoint units); later
                                #      slots reuse the last point; leave it out for stock
                                #      placement
```

Launch buttons now show the fighter plugin's `Label` rather than its file name.

### Smaller Quick Battle changes

- **Neutral allegiance.** A third *Add As Neutral Ship* button. A neutral spawns in neither
  mission group and gets no attack AI, so nobody targets it and it targets nobody (you can
  still shoot it). It is listed with an `[N]` prefix, and neutral carriers don't launch.
- **Keep-all-ammo whitelist.** Quick Battle strips every torpedo type after the first two
  from the *player's* ship. Hulls in `lKeepAllAmmo` keep their whole loadout. The list holds the
  Directive Fleet's hull names, so edit it for your own.
- **`OrionConfig.QuickBattleShip` is honoured.** Stock code forced a Galaxy as the starting
  player ship after the config value had been loaded. The value must be a Bedrock plugin
  name.
- **Compact ship and system lists.** The side-selector lists use the bitmap font one or two
  sizes down, and the three *Add as* buttons are shorter, so a big roster fits on screen.

---

## `BasicAttack.py.patch` (optional): let an AI fly its own cloak

Stock `BasicAttack` sends any cloak-equipped hull to `CloakAttackWrapper` and never looks
at its AIString. With this patch, if the hull's AIString module defines

```python
HANDLES_CLOAK = 1
```

then that module runs instead, and it is responsible for using the cloak itself. It
should only do so when the `UseCloaking` flag is set. Modules without the flag behave
exactly as stock. `DirectiveAttack` does **not** set it, so this patch does nothing until
you write an AI that does. `BasicAttack` is shared by every AI ship in the game, campaign
included, so leave it out unless you need it.

---

## Requirements

- **Star Trek: Bridge Commander** (retail or GOG, patched to 1.1).
- **Orion Delta RC4.1**. The patches were made against its `QuickBattle.py`, `MultiAttack.py`
  and `BasicAttack.py`. In RC4 those three files are byte-identical to RC4.1, so RC4 should
  work too. Delta's **Bedrock** ship system, which ships with Delta, handles ship
  registration. Delta has no `Foundation.py`, so Foundation-style `scripts/Custom/Ships/*.py`
  plugins do nothing there.
- To apply the patches: `git apply` (Git for Windows) or GNU `patch`.
- For the tools: Python 3.8 or newer, run outside the game.

The patches don't depend on any particular ship. The original Directive Fleet roster used
third-party hulls, which you get from their authors, not from here:

- **Axiom**: ships with Orion Delta.
- **Surak Refit** (`TNGFedSurak`): hull ported from *Star Trek: Legacy* by DarkDrone and
  Denacity.
- **Deep Space Nine (Mobile)** (`DS9Wings`): a novelty flyable copy of the Cardassian
  starbase.
- **Danube runabout**: ships with the game and Orion Delta.

---

## Install

Do all of this in **your install's** `Star Trek Bridge Commander` folder, with the game
closed.

1. **Back up** `scripts/QuickBattle/QuickBattle.py`, `scripts/AI/Compound/MultiAttack.py`
   and `scripts/AI/Compound/BasicAttack.py`. Keep the backups **outside**
   `scripts/Custom/Bedrock/Ships/` (see the gotchas below).
2. Make the fork's starting file. This copies `MultiAttack`; it does not move it:
   ```
   copy scripts\AI\Compound\MultiAttack.py scripts\AI\Compound\DirectiveAttack.py
   ```
3. Apply the patches. `BasicAttack` is optional:
   ```
   git apply --whitespace=nowarn path\to\patches\DirectiveAttack.py.patch
   git apply --whitespace=nowarn path\to\patches\QuickBattle.py.patch
   git apply --whitespace=nowarn path\to\patches\BasicAttack.py.patch
   ```
   With GNU patch: `patch -p1 < path/to/patches/QuickBattle.py.patch`, and so on. The game
   files use CRLF line endings, and so do the patches' content lines. Don't let an editor or
   git `autocrlf` re-save the `.patch` files.
4. **Delete the stale compiled files** beside anything you patched:
   `scripts/AI/Compound/DirectiveAttack.pyc` (if present), `BasicAttack.pyc`, and
   `scripts/QuickBattle/QuickBattle.pyc`. Also delete the `.pyc` of any hardpoint you edit.
5. Check the results:
   ```
   python tools/check152.py scripts/AI/Compound/DirectiveAttack.py scripts/QuickBattle/QuickBattle.py
   ```
6. Point a hull at the new brain. In its **hardpoint**, change `SetAIString("...")` to
   `SetAIString("DirectiveAttack")`, delete that hardpoint's `.pyc`, and make sure the hull
   has **no cloak** (or apply the `BasicAttack` patch and use an AI that handles the cloak).
7. Restart the game. Scripts are compiled on first import, so changes need a full restart.

To uninstall, restore your backups and delete `DirectiveAttack.py` and its `.pyc`.

---

## Gotchas that cost real evenings

- **`.pyc` shadowing.** The engine runs a stale `.pyc` sitting beside an edited `.py`. Always
  delete the `.pyc` after an edit. It also works the other way round: a mod's `.py` that lands
  beside an unrelated same-named `.pyc` gets recompiled and silently replaces an existing ship
  or script. Check for `.pyc` siblings before copying files in, and never copy a mod's `.pyc`
  files.
- **Roster sort order is part of your saves.** Bedrock assigns species IDs by the
  *alphabetical index* of the plugin file names in `scripts/Custom/Bedrock/Ships/`. A new
  plugin that sorts before an existing one shifts every later ID and breaks saves. Name new
  plugins so they sort **after the current last entry**, in byte order (uppercase before
  lowercase). That is why custom ships get `ZZ…` prefixes, and why the prefix grows as the
  roster tail grows.
- **Plugin file name == `ShipName` == `scripts/ships/<ShipName>.py`.** Ships are imported both
  as `Custom.Bedrock.Ships.<ShipName>` and as `ships.<ShipName>`. Rename one and you must
  rename all three, or the ship picker throws `ImportError`. The hardpoint, model and icon
  names are independent.
- **Keep nothing but `.py`/`.pyc` in `Custom/Bedrock/Ships/`.** The roster builder splits names
  at the first dot, so `Axiom.py.bak` turns up as a second "Axiom" and shifts species IDs. A
  sub-folder there is worse: it kills the whole roster build.
- **`Race = "..."` in a Bedrock plugin creates the Quick Battle race menu**, which is how a
  custom fleet gets its own menu. `SubMenu` groups hulls within it.
- **Python 1.5.2.** Use tabs only. It has no `True`/`False`, no list comprehensions, no
  `x += 1`, no string methods (use the `string` module) and no nested scopes. `continue`
  isn't allowed inside a `try` body. `None` is not a keyword, so a generated `None = ...`
  compiles and silently breaks every `!= None` test after it. `print` is a statement, so
  `sys.stdout.write` is the portable choice. The game also ships without a `traceback`
  module. `tools/check152.py` catches most of these, but a modern `py_compile` won't.
- **A wrong `SetFireSound` name fires silently.** It must match a name that is actually
  loaded in `Custom/Bedrock/Sounds/*.py`, not the projectile's name.

---

## Tools

```
python tools/check152.py <file.py> [more.py ...]
```

This flags Python 1.5.2 problems. It uses an AST check where Python 3 can parse the file,
and falls back to a regex scan for files that use 1.5.2 `print` statements. Exit code 1
means it found problems.

```
python tools/extract_ships.py --install "<your install>\Star Trek Bridge Commander"
```

This writes `tools/data/ships.json` and `torpedoes.json`. It also reads Orion Delta's
decompiled hardpoint corpus under `py/Orion/Ships/Hardpoints` when present. Each record
carries `ai` (the AIString), `cloak`, `race`, `wing`, and per-emitter weapon ranges and
damage. That shows you which hulls really run their AIString, i.e. those without a cloak.
**It executes every hardpoint file** against a fake `App` module, so only run it on scripts
you trust. A handful of hardpoints that import other hardpoints always fail; that's
expected.

---

## Test status

| Piece | Status |
|---|---|
| `DirectiveAttack`: no reset on interrupt | Measured in AI-vs-AI matches; the losing ship now fights back |
| `DirectiveAttack`: 220 range gate, 2 s / 2 s slice | Lightly tested. Early tests spawned the sides too far apart for a slow hull to reach range, so the gate's effect is not well measured |
| AI wing fixes | Checked offline against 57 carrier plugins (new slot logic selects exactly what the old did); an AI-flown carrier battle has not been confirmed in game |
| `FighterLaunchPoints` | Used in play on one carrier; AI-flown use untested |
| `AIFighterCap`, launch-button labels | Installed; in-game confirmation not recorded |
| Neutral allegiance | Verified in game |
| Keep-all-ammo, `QuickBattleShip`, compact lists | In use; no formal test |
| `BasicAttack` `HANDLES_CLOAK` | Tested offline only; no shipped AI uses it |
| `tools/*` | Run on Python 3.12 against an Orion Delta RC4.1 install |

The patches have only been tried on one heavily modded Orion Delta RC4.1 install. If a hunk
fails to apply, your `QuickBattle.py` differs from stock RC4.1, so compare by hand.

---

## Licence

The original work in this repo is MIT-licensed: the added and changed lines in the
patches, plus the tools. See [LICENSE](LICENSE). **The patches modify Totally Games code
(and Orion Delta's changes to it), and that code remains its owners'.** Neither the MIT
licence nor this repo grants any rights to it. The patches only contain enough of it as
context to apply.
