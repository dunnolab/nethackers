# NetHack Community Insights for NetHackers

**Date:** 2026-08-08
**Purpose:** Deep multi-source research pass on the NetHack *player community* — what it knows about winning, how it measures achievement, what its culture rewards — distilled into design guidance for NetHackers (LLM-evolved deterministic symbolic players built on AutoAscend, evaluated on seeded NLE 1.3.0 / NetHack 3.6.6, per-identity objectives, milestone-based fitness, ascension-rate north star, community leaderboard).

**Method:** ~25 targeted web searches; full-text retrieval of 40+ NetHackWiki pages (via Wayback Machine and reader-proxy — nethackwiki.com Cloudflare-blocks direct bot fetches, itself a lesson); direct fetches of tnnt.org, junethack.net, alt.org (NAO), hardfought.org, nethackscoreboard.org; NAO raw death statistics; the NeurIPS 2021 Challenge report; and the key papers (Insights from the NeurIPS 2021 NetHack Challenge; NetHack is Hard to Hack; Dungeons & Data; BALROG; NetPlay; Revisiting the NLE). Claims are cited inline; disagreements are flagged.

---

## 1. Ascension knowledge — what separates winning from dying

### 1.1 The shape of a winning game

The community's canonical phase model ([Standard strategy](https://nethackwiki.com/wiki/Standard_strategy)):

1. **Early game** (Dlvl 1 – ~10): survive; finish the two branches — **Gnomish Mines** (Minetown temple/shops for BUC + price ID; Mines' End luckstone) and **Sokoban** (guaranteed prize: bag of holding or amulet of reflection, plus wands/rings). Acquire **poison resistance** (else poisoned attacks can instakill) and start hunting **magic resistance + reflection**.
2. **Mid game**: reach XL 14 for the **Quest** (needs alignment record ≥ 20, impossible before turn ~2000); kill **Medusa** (needs reflection or blindness); do the Quest (Bell of Opening + quest artifact); conquer the **Castle** (wand of wishing in a tower; danger: arch-liches if not genocided and no MR).
3. **Late game**: **Gehennom** — ~20 maze levels; **prayer does not work**, **Elbereth does not work**, fire traps everywhere, covetous master/arch-liches; kill **Vlad** (Candelabrum), find the **vibrating square**, kill the **Wizard of Yendor** (Book of the Dead), perform the **invocation**, take the **Amulet** from the High Priest of Moloch.
4. **Ascension run / endgame**: climb ~50 levels back up against the **mysterious force** (25% chance per upstairs of being thrown back; chaotics suffer least — avg 32.7 staircase climbs vs 41.4 for lawfuls — a reason speedrunners carry a helm of opposite alignment) ([Mysterious force](https://nethackwiki.com/wiki/Mysterious_force)); then the four **Elemental Planes** (portal detection via confused scroll of gold detection; levitation for Air; crossing Water) and the **Astral Plane** (three high altars, the Riders, hordes of angels/priests/player-monsters) — sacrifice the Amulet on the co-aligned altar.

**Where games are actually decided:** Codehappy's analysis of 35,131 games (5,574 ascensions) by *expert* players (20+ ascensions / 8-streaks / sub-5-hour wins) found **"hardly anybody dies after the Castle"** — even for experts, nearly all deaths are early/mid-game; late game is execution, not risk ([codehappy.net expert-play data](https://codehappy.net/nethack/data.htm)). Expert overall win rate ≈ **15.9%**; **47.3%** on streak-continuing games; **70.7%** after 3 consecutive wins. The wiki agrees: "the early game is by far the hardest part... you could argue that starting difficulty is the only factor that matters" ([Role difficulty](https://nethackwiki.com/wiki/Role_difficulty)).

**Implication for NetHackers:** marginal fitness gains live disproportionately in Dlvl 1–15 survival and in mid-game gating milestones (Sokoban, Mines' End, Medusa, Castle). A bot that reliably reaches the Castle with a coherent kit is *most* of an ascender.

### 1.2 The survival toolkit a strong symbolic bot must encode

- **Elbereth (3.6.x semantics — many older spoilers are wrong).** Engraving "Elbereth" scares most melee monsters *on your own square only*. Since 3.6.0: must be the sole text on the square; **attacking while standing on it erases it** (−5 alignment, "You feel like a hypocrite"); each scare has a chance to erode a letter (100% dust, 1/26 semi-permanent, 1/52 burned); **does not work in Gehennom or the endgame**. Ignored by: all `@` (humans incl. shopkeepers, watch, player-monsters), all `A` (angels), minotaurs, the Riders, the Wizard, high priests, blinded monsters, and your own pets. Dust-engraving succeeds ~72.7%/attempt — pre-engrave, verify with `:`, don't wait until 1 HP. Burned (wand of fire/lightning) = permanent; athame/wand of digging = semi-permanent; Wizards get Magicbane (athame) as first gift. Pre-engraving retreat squares at choke points is expert practice. ([Elbereth](https://nethackwiki.com/wiki/Elbereth))
- **Prayer & prayer timeout.** #pray fixes "major problems" (HP < 1/7 max, starving, food poisoning, sliming, stoning, lycanthropy...) but only when *safe*: prayer timeout expired (base ~300–1000; praying with a major trouble tolerated down to timeout ≤ 200), alignment record ≥ 0, Luck ≥ 0, god not angry, **and not in Gehennom**. Successful prayer at low HP is the canonical early-game extra life (safe at turn 301+, "possible" at 101+). Water prayer on co-aligned altars makes holy water. Unsafe prayer = smiting, −3 Luck, escalating god anger. Naive "pray when low" without timeout tracking is a classic death spiral; expert play treats prayer as a budgeted resource and prefers real escape items. ([Prayer](https://nethackwiki.com/wiki/Prayer), [Why do I keep dying](https://nethackwiki.com/wiki/Why_do_I_keep_dying))
- **BUC & identification discipline.** Never wear/wield untested items (bones piles are "randomly cursed"); **pet curse-testing** (pets avoid cursed items); altar-drop BUC flashes; Priests see BUC innately ("walking altars"); **price identification** in shops (base-price tiers; e.g. the 20zm scroll of identify); **engrave-testing wands** (write Elbereth, add with the wand — most wands ID or hint; never bag an engraving-vanishing wand until cancellation is ruled out — it destroys bags of holding); dip-testing potions; monster-use identification. ([Identification](https://nethackwiki.com/wiki/Identification), [Beatitude](https://nethackwiki.com/wiki/Beatitude), [Why do I keep dying](https://nethackwiki.com/wiki/Why_do_I_keep_dying))
- **Altars & sacrifice.** BUC identification; sacrificing fresh corpses for luck, artifact gifts (Excalibur via lawful longsword-dipping at fountains; Mjollnir; Magicbane), pet-of-your-god co-aligned altar conversion; holy water manufacture; Minetown temple as early hub. Sacrificing a co-aligned unicorn = smiting (a YASD).
- **Food & corpse safety.** Corpses >60 turns old (or any zombie kill) = fatal food poisoning; kobolds poisonous; **cockatrice** corpse touch/eat = petrification (but a wielded corpse is a super-weapon with gloves); don't eat while Satiated (choking); Medusa's corpse kills; intrinsics-by-corpse schedule (poison/fire/cold/shock/sleep resistance, telepathy from floating eye — *never melee a floating eye*). ([Why do I keep dying](https://nethackwiki.com/wiki/Why_do_I_keep_dying))
- **The property checklist ("ascension kit").** Community-consensus *required*: **magic resistance** (GDSM, cloak of MR, Magicbane, or quest artifact) and **reflection** (SDSM, shield of reflection, amulet of reflection) — plus **free action**, **levitation source**, **unicorn horn** (cures status ailments; "an absolute must"), **luck item (blessed luckstone)**, high **MC (magic cancellation, MC3 ideally)**, ranged attack, **conflict**, portal detection (confused gold detection scrolls), escape items (wands of teleportation/digging), holy water, full healing potions, cursed potions of gain level (Sanctum escape), 7 candles, K/C-rations for Famine, lizard corpse vs stoning; amulet of life saving as blunder insurance. Standard armor solutions are tabulated combos of {S/GDSM × cloak × amulet × shield} trading twoweapon vs life-saving vs displacement vs spellcasting. ([Ascension kit](https://nethackwiki.com/wiki/Ascension_kit))
  - **Data check:** codehappy found early **reflection** wishes outperform early **MR** wishes (61.7% vs 55.4% ascension) — early-game killers (dragon breath, wand rays) are reflectable, while MR's threats come later. Community writing tends to say "MR first"; the data disagrees for the early game. ([codehappy](https://codehappy.net/nethack/data.htm))
- **Resistances/intrinsics timeline.** Poison resistance ASAP (Barbarian/Healer/Orc start with it; Monk at XL3); fire resistance before Gehennom; sleep/shock/cold before Castle-ish; telepathy for planning; intrinsic speed (wand/potion/quest) — "very fast" is a defining expert advantage. ([Intrinsic](https://nethackwiki.com/wiki/Intrinsic), [Standard strategy](https://nethackwiki.com/wiki/Standard_strategy))
- **Movement/attention hygiene** (humans die to this; bots get it free but must implement the checks): never melee at low HP, watch Weak/Fainting, don't fight adjacent to water (drowning grabs are instadeath — grease/oilskin protects), don't quaff from fountains early (water demons/moccasins are top-20 NAO killers), beware level-appropriate spikes (soldier ants, dwarves with wands).

### 1.3 YASD — the canonical stupid deaths (what the community warns about)

From [Yet Another Stupid Death](https://nethackwiki.com/wiki/Yet_Another_Stupid_Death) (plus YAAD = unavoidable "annoying" deaths; DYWYPI = "Do you want your possessions identified?", the death prompt used as a synonym for dying):

- Meleeing a **floating eye** → paralyzed → nibbled to death by a newt.
- Melee at very low HP; exploring after loading a save at low HP.
- **Fountain quaffing** → water demon / water moccasin swarm.
- Any bare-handed interaction with a **cockatrice** (corpse, egg, hidden mimic).
- Starving/fainting unnoticed; **choking** ("You're having a hard time getting all of it down") while Satiated eating for intrinsics.
- Eating rotten/old corpses right after prayer; eating Medusa ("hey, a melon!").
- **Self-zapped bounced rays** (wand of death at a wall); genociding your own race; cursed self-genocide.
- Drowning/lava; kicking things at low HP; riding your Knight's pony at XL1; wishing for a cross-aligned artifact (it blasts you); dying with an *unworn* amulet of life saving in the pack; Monk wearing the Eyes of the Overworld pre-Medusa (spoiler-trap); foocubus at XL1; "forgetting the power of Elbereth".

**Ground truth from 8.6M NAO games** ([NAO top deaths](https://www.alt.org/nethack/topdeaths.html)): the top killers are almost all **early-game trash**: jackal (0.83%), dwarf (0.80%), gnome lord, **soldier ant** (0.67%), sewer rat, giant bat, small mimic, gnome, fox, **water moccasin** (0.54%, fountains), rothe, "killed by a wand" (0.53% — early dwarves with striking/fire), *slipped while mounting a saddled pet* (0.50%!), giant ant... **"ascended" ranks 15th at 0.394% of all games.** Notable named causes deeper in the list: rotted-corpse poisoning (0.37%), water demon (0.37%), shopkeeper (0.35%), starvation (0.13%), choking (0.12%), cockatrice petrification (0.10% + corpse-touch variants), scroll of genocide (0.075%), brainlessness (mind flayers, 0.035%), drowning eels (0.035%), own-ball-of-lightning/fireball class self-kills, touch of death (0.017%). This is a precise target list for bot defenses and for LLM-mutation focus.

---

## 2. Per-character difficulty (per-identity objectives)

### 2.1 Community consensus, with reasons

From [Role difficulty](https://nethackwiki.com/wiki/Role_difficulty) (cross-checked with the role pages and Quora/forum discussions):

| Tier | Roles | Why |
|---|---|---|
| Easiest | **Valkyrie** (esp. dwarven) | Best starting melee, high Str/Con, cold res, Excalibur access, easy quest, Orb of Fate; the canonical beginner/streak/bot role. First-ever bot ascension (BotHack's smartbot3, 2015) was a **lawful female dwarven Valkyrie**. |
| Easy | **Barbarian**, **Samurai** | Barbarian: poison resistance from XL1 (removes a whole instadeath class), two-hander, though hostile Mines (orc race) and weak quest artifact. Samurai: strong melee+missiles (multishot with daikyu/ya), but riskier quest and mid-game lull. |
| Strong-but-different | **Knight** | Excalibur early, best late-game (>100-dmg magic missiles), but steed micromanagement and conduct/alignment friction; "slipped while mounting" is literally a top-13 NAO death. |
| Middle | **Monk** (easy early, tricky mid: armor restrictions, hard quest), **Ranger**/**Rogue** (strong if ranged-attack discipline is good — "powerful ranged attacks, especially in the early game"), **Caveman** ("more difficult barbarian", no #twoweapon, weak weapons until Sceptre of Might), **Priest** (innate BUC-sight + holy water = "walking altar", but edged-weapon restrictions, no multishot, random spells) |
| Hard early | **Healer** (poison res + gold + healing, but single-digit Str, bad AC/weapon; classic **protection racket** role), **Archeologist** (speed+stealth+pick-axe+touchstone but awful combat stats), **Wizard** (great kit — Magicbane, Eye of the Aethiopica, spells — but fragile; "easy" only if actually played as a caster), **Tourist** (worst start: bad stats/AC, Hawaiian shirt aggro, +2 darts as only edge; excellent *late* game via Platinum Yendorian Express Card) |

**Races:** dwarf (best Valk), orc (poison res, infravision, cannibalism-free), gnome (Mines peaceful), elf (sleep res, better casters), human (no restrictions, needed for Kni/Mon/Sam/Tou). **Alignment matters at the margin:** chaotics suffer the mysterious force least (see §1.1); lawfuls get Excalibur. TNNT counts **73 valid role/race/alignment/gender starting combos** — the natural cardinality for NetHackers' identity×progress map ([TNNT trophies](https://tnnt.org/trophies)).

### 2.2 What the data says (and how it disagrees with folklore)

NAO statistics on [Role difficulty](https://nethackwiki.com/wiki/Role_difficulty):

- **Streak-conditioned win rates** (games that would extend a ≥3 streak, i.e. serious players trying to win, 2011): Valkyrie **62.6%**, Barbarian **61.0%**, Samurai 53.9%, Archeologist 50.5%, Caveman 49.0%, Monk 49.0%, Ranger 48.1%, Knight 47.4%, Healer 45.5%, Priest 43.1%, Wizard 42.6%, Rogue 41.8%, **Tourist 38.3%**.
- **Raw unbiased win rates** (all NAO games 2008–2017, excluding quit/escape): Caveman 4.60%, Samurai 2.97%, Knight 2.79%, Barbarian 2.58%, Healer 2.47%, Ranger 2.39%, Rogue 1.92%, Tourist 1.77%, Archeologist 1.76%, Priest 1.74%, **Valkyrie 1.70%**, Monk 1.67%, **Wizard 1.08%** (n=330k games!).
- The two tables *disagree* because of selection effects the wiki itself calls out: Valkyrie/Wizard are what novices and start-scummers pick (Wizard has the most games by far and the worst raw rate); Caveman is picked almost only by completionists/tournament veterans. **Lesson: population win rates are hopelessly confounded; conditioning on "trying to win" (streak-eligible games) is the community's accepted normalizer.** For NetHackers' per-identity difficulty priors, the streak-conditioned ordering is the better calibration target, and the raw table is a warning about leaderboard metric design.
- Bot-side confirmation: in the NeurIPS 2021 Challenge, all agents (including AutoAscend) scored best on **Barbarian/Monk/Samurai/Valkyrie** and worst on **Healer/Rogue/Tourist/Wizard** ([Insights paper](https://arxiv.org/abs/2203.11889)) — i.e., bots amplify the human early-game ranking because they can't exploit the caster/late-game upside that makes Wizards good *for humans*. Expect the NetHackers progress map to be conquered in roughly this order, and treat Wizard/Tourist/Healer columns as the high-value research frontier.

### 2.3 Starting-inventory notes that matter for per-identity bot code

- Valkyrie: +1 long sword, +0 dagger, small shield → mindless melee viable.
- Samurai: katana + short sword + **yumi with 24-33 ya** → multishot ranged loop.
- Barbarian: two-hander + ring mail + **poison resistance**.
- Ranger: +1 bow + 50-60 arrows + cloak of displacement.
- Rogue: 9-10 **daggers** (multishot class bonus) + lock pick + stealth.
- Tourist: 20+ +2 **darts**, expensive camera (blinding), Hawaiian shirt (shop markups!), credit card, 25% magic marker.
- Wizard: Force Bolt/random spellbook, cloak of MR (!), quarterstaff, random wand/ring/potion; start-scummable variety.
- Healer: stethoscope (free HP checks — bots should spam this), healing potions, **~1000-1400 gold** (protection racket), poison res.
- Priest: holy water ×4, **innate BUC sight**, mace.
- Monk: martial arts (best unarmed), sleeping gas grenade-ish spells vary; no body armor.
- Archeologist: **pick-axe** (dig-for-victory routes), tinning kit, touchstone.
- Knight: pony + lance (jousting), apple/carrot pet management.
- Caveman: club + sling + flint stones, rocks.
(Compiled from role pages / [Role difficulty](https://nethackwiki.com/wiki/Role_difficulty).)

---

## 3. Where bots/AI struggle vs humans

### 3.1 Pre-NLE bot history (the symbolic lineage NetHackers extends)

- **Trivial bots**: pudding-farming Perl scripts; eit_brad's 2003 paste-loop that overflowed the 32-bit score counter in /dev/null ([Bot](https://nethackwiki.com/wiki/Bot)).
- **TAEB** ("There's An Elf Betwixt us", Perl framework, pluggable AIs; also produced **Interhack**, an interface-enhancement proxy layer — HP alerts, safe-to-pray indicator, corpse-safety annotations — i.e., the community already built "harness" tooling for humans: [taeb.github.io](https://taeb.github.io), [Interhack](https://taeb.github.io/interhack/)).
- **Saiph** (C++), **Demonia**, and finally **[BotHack](https://github.com/krajj7/BotHack)** (Clojure, plays unmodified NetHack over terminal): **first full-auto bot ascension, 2015-01-25** (smartbot3, lawful female dwarven Valkyrie, on vanilla NetHack 3.4.3 hosted at acehack.de — i.e. the old-strength-Elbereth era) ([Bot](https://nethackwiki.com/wiki/Bot), [YAAP announcement](https://groups.google.com/g/rec.games.roguelike.nethack/c/TOoX7ptqBEQ)). BotHack shipped reusable solvers: Sokoban, auto-identification, navigation, monster tracking — the same decomposition AutoAscend re-implements.
- Community verdict pre-2015 (rgrn): "a bot is in theory possible, but tremendously difficult." Post-2015: possible, on the community's easiest identity, with heavy Elbereth abuse.

### 3.2 NeurIPS 2021 Challenge & AutoAscend — the current baseline

From the [challenge report](https://nethackchallenge.com/report.html) and the [Insights paper](https://arxiv.org/abs/2203.11889):

- Setup: NLE (NetHack 3.6.6), **random role/race/alignment/gender each episode**, ranked by **mean ascensions**, tie-broken by median score; 4096-episode final eval; 1M steps / 30min caps. 483 entrants, 631 submissions.
- **No agent ascended in >500,000 evaluation games.** Top median score ≈ 5,000–5,300 = "just above Beginner". Symbolic ≈ 3× neural median (5× on top episodes); best RL was ~5× better than the 2020 baseline but still far behind AutoAscend.
- **Score-vs-progress pathology:** many entrants "camped" early levels grinding score instead of descending — the report explicitly flags that score optimization diverges from ascension. (NetHackers' milestone/ascension-anchored fitness is the community-endorsed fix.)
- **AutoAscend internals** (per [NetHack is Hard to Hack](https://arxiv.org/abs/2305.19240), which instrumented it): ~**11 top-level strategies** in a DAG with predicate-based control flow (explore, fight, eat/food-safety, item management, altar interactions, guard-following, Sokoban solver, ...), subroutine depth ≤ 5. Over 3,402 seeded games: **mean score 8,556; median 4,918; mean final Dlvl ≈ 3.1; mean survival ≈ 19,600 turns**. Behavior is bimodal: long Dlvl-1 camping vs dives to ~Dlvl 11. Uses hand-crafted parsers; brittle to unexpected states ("custom input may confuse the agent... exceptions", per its [README](https://github.com/maciej-sypetkowski/autoascend)).
- **Known AutoAscend gaps to target** (synthesis of the above + §1–2): no real spellcasting (hence Wizard/Healer/Priest underperformance), no wish/kit planning, no Quest/Gehennom/Planes logic at all (never gets there), shallow shop/priest interactions, altar use is "farming"-level not kit-building, Elbereth used tactically but no prayer-budget strategy, no Medusa/Castle handlers, limited BUC/ID pipeline vs the community's full discipline in §1.2. Every §1 milestone beyond the Castle is effectively unwritten code.

### 3.3 Neural / LLM attempts (why NetHackers' "LLM evolves symbolic code" bet is right)

- Imitation on 100k AutoAscend games (NLD-AA, 3B actions): best neural agents reach mean ~1,151–1,551 vs teacher's 8,556; scaling laws **asymptote ~1,000** — "data alone cannot close the gap"; neural agents can't reproduce long-horizon low-level behaviors even factorized per-strategy ([NetHack is Hard to Hack](https://arxiv.org/abs/2305.19240)).
- **NetPlay** (GPT-4 zero-shot agent): flexible at instructed subtasks, "struggles with more ambiguous tasks, such as winning the game"; a simple rule-based agent matches it ([arXiv:2403.00690](https://arxiv.org/abs/2403.00690)).
- **BALROG** (ICLR 2025): LLMs/VLMs as direct policies barely progress; introduced the **data-informed progression metric** — map (deepest dungeon level 1–50, experience level 1–30) → human win-probability from the Dungeons&Data NAO corpus, normalized 0–100 ([arXiv:2411.13543](https://arxiv.org/abs/2411.13543), [docs](https://balrog-ai.github.io/docs/envs/nle.html)). This is the exact lineage of NetHackers' `nle-progress`-style fitness.
- 2026 state of the art for direct LLM play: GPT-5.2 with a Python-sandbox harness reached **Dlvl 10, BALROG 12.6%** — the deepest any LLM had reached; failure modes: ASCII spatial reasoning, long-horizon planning, phantom-threat loops ([kenforthewin blog](https://kenforthewin.github.io/blog/posts/nethack-agent/)). Meanwhile a decade-old symbolic bot has *ascended*. LLM-as-code-evolver over a symbolic substrate (NetHackers' architecture) sits exactly in the gap: symbolic reliability + LLM domain-knowledge injection, which the Challenge report lists as symbolic bots' decisive advantages (strategy encoding, persistent memory, external knowledge).
- **LuckyMera** (modular hybrid framework over NLE, 2023) independently converged on the same architecture: symbolic skill modules + learned modules, arguing for modular "behavior library" designs ([arXiv:2307.08532](https://arxiv.org/abs/2307.08532)).

### 3.4 Tasks the community itself flags as automation-hard

Sokoban (solved-ish: AutoAscend/BotHack ship dedicated solvers; mind the 3.6 luck-penalty rules and boulder-mimics — [Sokoban](https://nethackwiki.com/wiki/Sokoban)); **altar/BUC logistics** (multi-step, stateful); **shopkeeper protocol** (debt, credit, price-ID, never anger; shopkeepers ignore Elbereth and are a top-20 killer); **priest donations** (protection buying); **the Quest** (per-role bosses, XL14 + alignment gate); **Gehennom navigation** (mazes, digging strategy, no-prayer/no-Elbereth combat, covetous liches, demon-lord bribes/fights); **vibrating square search**; **Wizard-of-Yendor harassment loop** (re-kills, item theft, Double Trouble); **mysterious-force ascension-run routing**; **Planes** (portal detection, Air levitation, Water crossing, Astral altar identification + Riders). Each is a well-specified, testable module — ideal units for LLM mutation with seeded regression suites.

---

## 4. How achievement is measured & celebrated

### 4.1 The reality of ascension rates (calibration anchors)

- All NAO games ever: **~0.4% end in ascension** (33,978 of ~8.6M finished games; [NAO top deaths](https://www.alt.org/nethack/topdeaths.html)).
- NLD-NAO dataset (1.5M human games 2009–2020): **~22k ascensions ≈ 1.5%** ([Dungeons & Data](https://arxiv.org/abs/2211.00539)).
- Per-role raw NAO rates: 1.1–4.6% (§2.2). Expert accounts: **15.9%** overall, 47.3% streak-continuing ([codehappy](https://codehappy.net/nethack/data.htm)).
- First ascensions typically take **50k–100k turns**; well-spoiled players <50k/<30k; "speedrun" begins <20k ([Speed ascension](https://nethackwiki.com/wiki/Speed_ascension)).

### 4.2 Beyond winning: the community's achievement taxonomy

- **Streaks** (consecutive wins, same account): the premier skill signal — only **9.6% of winning NAO accounts** ever streak ≥3 ([Streak](https://nethackwiki.com/wiki/Streak)). Records: **Tariru 61** consecutive 3.6.0 ascensions (and 200+ wins/year); Adeon 29 (3.4.3); Marvin 24; Grasshopper 19 *Elberethless* ([Notable players](https://nethackwiki.com/wiki/Notable_players)). TNNT streak rule worth copying: "first game started after a win is the streak candidate" (permits parallel games without breaking streaks) ([TNNT FAQ](https://tnnt.org/faq)).
- **Conducts** (in-game-tracked voluntary challenges since 3.3.0): foodless / vegan / vegetarian / atheist / weaponless / **pacifist** / illiterate / polypileless / polyselfless / wishless / artifact-wishless / genocideless (+3.6 options: **zen** blindfolded, nudist; 3.7 adds permadeaf, petless, pauper...) ([Conduct](https://nethackwiki.com/wiki/Conduct)). Achievement rates among NAO *winning accounts*: polyselfless 73.5%, polypileless 42.3%, genocideless 33.1% (easy tier) vs the hard tier — pacifist ("one of the more difficult"), foodless (prayer-cycling + ring of slow digestion), zen ("only a handful have ascended") — and legendary combo runs like Marvin's 8-conduct games and SolarFlare's bones-free 9-conduct Vampire Convict in variants ([Notable ascensions](https://nethackwiki.com/wiki/Notable_ascensions)). **Conducts are literally alternative objective functions the game already tracks in the xlogfile** — free leaderboard categories for NetHackers.
- **Speedrun categories:** low **turncount** (floor: 2,000 turns because of the Quest alignment gate; human records ~2,130–2,135 [Maud, SpeedyCat7]; ais523's RNG-manipulated TAS target ≈ 2,017; best roles: Wizard, neutral Monk) and **realtime** (record 0:52 on NetHack 3.6.6 by Luxidream; **0:49 on a *seeded* run** — seeded speedrunning is already a recognized community category, precedent for NetHackers' seeded evals) ([Speed ascension](https://nethackwiki.com/wiki/Speed_ascension)). Also **low-score ascension** ("more prestigious — you got the job done with less effort"; theoretical minimum 12,200 in 3.6.x) and its opposite, MAXINT score runs ([Score](https://nethackwiki.com/wiki/Score)).
- **Z-score** (cross-game diversity metric used by TNNT/NAO/nethackscoreboard): 1st ascension of a role = 1 pt, 2nd = 1/2, 3rd = 1/3... — 13 unique-role wins = 13.00, but 13 Valkyries = 3.18 ([TNNT FAQ](https://tnnt.org/faq)). **Directly reusable as NetHackers' "coverage" score over the identity map.**

### 4.3 Tournament design lessons (20+ years of iteration)

- **/dev/null/nethack (1999–2016)**: November institution. Its **trophy star-ladder is a community-validated milestone ordering**: Plastic (complete Sokoban) → Lead (Mines' End) → Iron (quest nemesis) → Zinc (Medusa) → Copper (enter Gehennom) → Brass (Vlad) → Steel (Wizard) → Bronze (invocation) → Silver (Amulet from High Priest) → Gold (reach Planes) → Platinum (reach Astral) → Dilithium (ascend). Grand trophy "**Best of 13**": most ascensions in 13 consecutive games with **no repeated race/role/alignment/gender combo** (Berry once did 13/13, all MAXINT). Recognition combos: Birdie (both genders) → Double Top → Hat Trick → Grand Slam (all genders+roles+races+alignments) → **Full Monty** (+ all conducts), each with a consecutive-games "with bells on" variant. Annual secret "challenges" (KoL, Grue, Pac-Man, ZAPM crossovers) kept it fresh. ([devnull tournament page](https://nethack.fandom.com/wiki//dev/null/nethack_tournament))
- **TNNT (2018–, hardfought servers, vanilla 3.6.7 + achievements patch; [tnnt.org](https://tnnt.org)):** 2024: 335 players, 112 ascenders. Key design decisions with stated rationale ([FAQ](https://tnnt.org/faq), [wiki page](https://nethackwiki.com/wiki/The_November_NetHack_Tournament)):
  - **Abandoned unified scoring in 2021** — "biases in the scoring algorithm... no objective score on what was ultimately a subjective measure" — replaced by **16 parallel leaderboards** (Most Ascensions, Earliest Ascension, Lowest Turncount, Fastest Realtime, Most Conducts in One Ascension, Most/Most-in-one Achievements, Lowest/Highest Scoring Ascension, Longest Streak, **Most Unique Deaths**, Most Unique Ascension Combos, Highest Z-score, **Most Post-Amulet Splats**, Most Swap-Chest Donations, Most Games >1000 turns). Response "generally positive... compete in categories they like."
  - **Hundreds of in-game achievements** (325 as of Nov 2023 per the wiki; 368 listed on tnnt.org in 2026) with in-game progress commands (#achievements, #tnntstats) and near-real-time website sync.
  - **44 trophies** incl. the per-identity ladder: Great/Lesser {Race} and {Role} (Lesser = *complete Mines + Sokoban* with every combo — an explicit intermediate-milestone version of ascension trophies), All Roles/Races/Alignments/Genders/Conducts/Achievements, **NetHack Master (all 73 combos)**, **NetHack Dominator** (Master + all conducts), and flavor challenges (Keep Vlad/Rodney/Nemesis/High-Priest/Riders Alive; Never Scum a Game).
  - **Clans** (≤12, "designed so adding new clan members, however inexperienced, cannot hurt the clan" — deliberately no clan ascension-ratio metric), swap chest (cross-player item gifting), cross-server bones exchange, October open beta as community co-design.
  - **Bots are banned** ("TNNT is a tournament for human players... any system that automatically evaluates game state and makes gameplay decisions"). Same on most public servers' tournaments. **There is no bot tournament — NetHackers can own that niche** (nearest precedent: aicrowd's one-off 2021 challenge).
- **Junethack (2011–, [junethack.net](https://junethack.net)):** June, **cross-variant** (24+ variants incl. NetHack 5.0.0, UnNetHack, EvilHack...; 2026: 193 players, 17,694 games). Trophies: per-variant ascension/all-roles/all-races/all-conducts, cross-variant meta-trophies (Sightseeing Tour, Globetrotter, Diversity Ascender), competitions for fastest (turns & wallclock), highest/lowest score, most conducts, longest streak, and clan awards incl. **"Most unique deaths"** — dying creatively is a celebrated objective.
- **NetHackathon** (2021–, twice yearly): Twitch streamers relay-play a single shared character for 48+ hours ([hardfought tournaments](https://www.hardfought.org/nethack/tournaments/)) — a made-for-spectacle format NetHackers could mirror ("relay evolution": successive harness generations continue one seed).
- **[NetHack Scoreboard](https://nethackscoreboard.org)**: aggregates xlogfiles from 60+ server/variant sources into cross-server player pages, streaks, Z-scores, conducts, unique deaths — proof that **xlogfile is the community's interchange format**; emitting xlogfile-compatible records from NLE runs would let NetHackers piggyback on existing tooling and mental models.

---

## 5. Community culture & virality — what pulls people in

### 5.1 The infrastructure of belonging

- **Public servers**: [NAO](https://alt.org/nethack/) (running since the early 2000s; **166,045 registered names, 8,634,240 finished games**; telnet/ssh/browser play; hosts 3.6.x and now NetHack 5.0.0) and [Hardfought](https://www.hardfought.org) (US/EU/AU, founded 2000, dozens of variants, TNNT home). dgamelaunch accounts = persistent identity.
- **Radical transparency by default**: every game recorded as **ttyrec** (replayable terminal capture), **dumplogs** (endgame character sheets, linkable), **xlogfiles** (machine-readable result lines), live **spectating** of any in-progress game (NAO "TV", termcast) — the community's entire status economy is built on public, linkable, replayable evidence. NAO ships bulk archives of *all* ttyrecs/dumplogs for download (that's how NLD-NAO exists).
- **Bones files**: your death leaves your ghost + cursed loot in *someone else's* game ("You feel a strange presence" moments; hardfought exchanges bones **between servers**). Asynchronous multiplayer via corpses — a unique social texture; players even set `!bones` out of courtesy when scumming ([TNNT FAQ](https://tnnt.org/faq), [Bones](https://nethackwiki.com/wiki/Bones)).
- **IRC as the town square**: #nethack / #hardfought / #tnnt / #junethack on Libera; the **Rodney** bot announces every death/ascension "to the morbid delight of all", answers lore queries, holds a community quote/fact database; Croesus does the same for hardfought; **livelog** events (artifact found, luckstone picked up) stream in real time. Deaths are *broadcast entertainment*.
- **The wiki** ([nethackwiki.com](https://nethackwiki.com)) as canonical shared brain — strategy pages double as social artifacts (Notable players, Notable ascensions, YASD, Bad ideas). Spoiler culture is embraced ("well-spoiled" is a compliment; classic spoiler compendia: [steelypips conduct spoiler](https://www.steelypips.org/nethack/conduct.html), Eva Myers' spoiler list).
- **Lore & memes**: **TDTTOE** ("The DevTeam Thinks Of Everything" — every clever idea has a coded response; it became TVTropes' "Developers' Foresight"); **DYWYPI**; YASD/YAAP/YAFAP post genres on rec.games.roguelike.nethack (the community's original home); **Dudley's Dungeon** webcomic; the Kingdom-of-Loathing crossovers; "splat"; "Rodney" as the Wizard's nickname.

### 5.2 Why it retains people (mechanics of stickiness)

1. **Failure is content.** Deaths are named, ranked ("Most Unique Deaths", "most egregious deaths" page on NAO), broadcast by bots, and memorialized in bones. Shame → shared comedy.
2. **A ladder of attainable sub-goals** below the near-impossible win (devnull stars, TNNT Lesser trophies, 325 achievements) — everyone leaves a tournament with *something*.
3. **Identity-completion collecting** (Great Roles/Races, NetHack Master's 73 combos, Z-score) gives veterans an endgame beyond one win.
4. **Public records + personal records**: streaks, turncounts, realtime, conducts create many orthogonal "world record" niches (Maud = turncount, Luxidream = realtime, Tariru = streaks, stth = extinction, Marvin = conducts).
5. **Teams designed to be welcoming** (clan scoring can't be hurt by weak members).
6. **Ritual cadence**: June (variants) and November (vanilla) every year since 1999/2011/2018; beta month as co-design.
7. **Everything is linkable**: a dumplog URL or ttyrec is a trophy you can post to rgrn/Reddit/IRC.

### 5.3 Concrete virality lessons for NetHackers

- Publish **every run** as a linkable artifact (seed, identity, dumplog-style summary, replay — NLE ttyrec-style recordings) — no private results. Adopt/emit **xlogfile** format for interop with nethackscoreboard-style tooling.
- Build the leaderboard as **many small ladders, not one score** (TNNT's explicit, battle-tested lesson), anchored by the milestone star-ladder (devnull's ordering) per identity; add Z-score-style **coverage** over the 73-combo map, streaks (consecutive seeded wins), lowest-turncount, conduct boards, and a **"Most Unique Deaths" / best-YASD board** — celebrating bot failures is both diagnostic and on-culture.
- **"Impact" = credited lineage**: the community credits named individuals for records; mirror that by crediting harness authors/mutations on every milestone first ("first Tourist Sokoban completion", "first Gehennom entry by an evolved Wizard").
- Run a **recurring tournament window** (e.g., "BotNovember" complementing human-only TNNT) rather than a static board; add spectacle events (relay runs à la NetHackathon; live "bot TV" of best seeds).
- Wire a **death/ascension announcer bot** into Discord/IRC from day one; it's the community's proven engagement loop.
- Bones-like cross-pollination between harnesses (share discovered item knowledge? seed-specific hint files) would be a novel, on-brand mechanic — but keep the *evaluation* bones-free for determinism (see §6).
- Mind access norms: nethackwiki Cloudflare-blocks bots and TNNT bans them — engage as good citizens (publish research, don't scrape aggressively, don't run bots on human servers/tournaments; NAO's rules and the TNNT FAQ are explicit).

---

## 6. NLE vs. real NetHack — eval-fidelity notes

- **Version pinning:** NLE is built on **NetHack 3.6.6** (released 2020-03-08; only security/bug fixes over 3.6.5 — no gameplay change) ([NLE repo](https://github.com/facebookresearch/nle), [NetHack 3.6.6](https://nethackwiki.com/wiki/NetHack_3.6.6)). The community currently plays 3.6.7 (TNNT: "current stable"), 3.7-dev, and since 2026 **NetHack 5.0.0** on NAO — so *current-wiki advice increasingly carries 3.7+ annotations that do NOT apply to 3.6.6*. Wiki pages flag these with "pertains to an upcoming version (3.7.0)" boxes — e.g., 3.7's poison no longer instakills, unicorn horns no longer restore attributes, guaranteed Castle gain-level potion, weaker mysterious force, quest-before-turn-2000 via leader-kill, Elbereth ignored by all uniques. **A spoiler-ingestion pipeline for the LLM mutator must filter by version tag.**
- **Equally: pre-3.6 spoilers are stale.** The big 3.4.3→3.6.x deltas that matter to a 3.6.6 bot: Elbereth nerf (§1.2 — old bots like BotHack leaned on mechanics that no longer exist); Sokoban prize now 50/50 per map *with a cursed scroll of scare monster under it* and relaxed luck-penalty rules; **pudding farming killed** (puddings leave unsacrificeable globs — the historic score exploit is closed in-engine); mimic-corpse polyself counts against polyselfless; conducts/options additions (blind/nudist); vibrating square marked once found (3.6.1+... note: *3.6.0 did not mark it* — 3.6.6 does).
- **Challenge-standard evaluation** (the comparability baseline for AutoAscend numbers): random `@` character (role/race/align/gender), mean-ascensions-then-median-score ranking, 1M step / 30-minute caps ([aicrowd rules](https://www.aicrowd.com/challenges/neurips-2021-the-nethack-challenge/challenge_rules)). Note **base NLE tasks default to `mon-hum-neu-mal` (a Monk)** — published "NLE score" numbers are often Monk-only and not comparable to random-character or per-identity runs; always state the character spec.
- **NLE interface ≠ full game** ([Revisiting the NLE, ICLR 2026 blogpost](https://iclr-blogposts.github.io/2026/blog/2026/revisiting-the-nle/)): 121-action keyboard space; menus/inventory interactions are awkwardly observed (menu text conflated with map glyphs; no explicit menu-open signal); some info humans read from the UI (race/role/alignment) must be inferred; observation = glyphs/chars/colors/blstats/messages/inv arrays, not the raw terminal. Symbolic bots parse around this (AutoAscend ships parsers), but harness code inherits these seams — a known source of "exception" deaths.
- **Determinism/seeding:** NLE exposes core+display seeds (with `reseed=False` for reproducibility) — the foundation of NetHackers' seeded evaluation; the community already recognizes **seeded categories** as legitimate-but-separate (Luxidream's 0:49 "seeded" is listed apart from his 0:52 record; UnNetHack ran "set seed" speedrun events). Keep seeded and unseeded (random-seed generalization) boards separate, as the community does.
- **No bones, no multiplayer texture:** NLE episodes don't load bones or interact with other players — removes both variance (bones can be windfalls or deathtraps) and a comparability gap vs server play. Also verify NLE's default `nethackrc` (autopickup and pickup_types are set by the env, not by "server defaults") before porting human advice that assumes manual pickup discipline.
- **Score is officially untrusted:** in-engine score is gameable (camping, gold, MAXINT exploits; §4.2's "low score is prestigious") and the Challenge/BALROG/blogpost all converged on progression-style metrics — dungeon-depth+XL→human-win-probability (BALROG) or exploration ("scout") — validating NetHackers' calibrated milestone fitness. Anchor the calibration to **NLD-NAO** human data ([Dungeons & Data](https://arxiv.org/abs/2211.00539): 1.5M games, ~22k ascensions, 10B transitions; companion NLD-AA: 100k AutoAscend games for imitation/regression baselines).

---

## 7. Useful resources & tools (annotated link list)

**Canonical knowledge**
- NetHackWiki (3.6.x-aware, version-tagged): https://nethackwiki.com — key pages: [Standard strategy](https://nethackwiki.com/wiki/Standard_strategy), [Ascension kit](https://nethackwiki.com/wiki/Ascension_kit), [Role difficulty](https://nethackwiki.com/wiki/Role_difficulty), [Elbereth](https://nethackwiki.com/wiki/Elbereth), [Prayer](https://nethackwiki.com/wiki/Prayer), [Conduct](https://nethackwiki.com/wiki/Conduct), [YASD](https://nethackwiki.com/wiki/Yet_Another_Stupid_Death), [Why do I keep dying](https://nethackwiki.com/wiki/Why_do_I_keep_dying), [Sokoban](https://nethackwiki.com/wiki/Sokoban), [Gehennom](https://nethackwiki.com/wiki/Gehennom), [Astral Plane](https://nethackwiki.com/wiki/Astral_Plane), [Speed ascension](https://nethackwiki.com/wiki/Speed_ascension), [Score](https://nethackwiki.com/wiki/Score), [Notable players](https://nethackwiki.com/wiki/Notable_players). *(Cloudflare-guarded against bots; mirror via Wayback or the stale-but-open 3.4.3-era fork at nethack.fandom.com.)*
- Classic spoilers: steelypips.org conduct spoiler (https://www.steelypips.org/nethack/conduct.html), Eva Myers' spoiler list (linked from NAO), Maniac's Ascension Guide (https://www.alt.org/nethack/mirror/homepage.mac.com/mhjohnson/mag-r342.html), NetHack Guidebook (https://www.nethack.org).
- Expert-play statistics: https://codehappy.net/nethack/data.htm.

**Servers / community stats**
- NAO: https://alt.org/nethack/ — [top deaths](https://www.alt.org/nethack/topdeaths.html), [ascension streaks](https://alt.org/nethack/ascstreak.html), Z-scores, per-role score stats, bulk ttyrec/xlogfile archives, Rodney (https://www.alt.org/nethack/Rodney/).
- Hardfought: https://www.hardfought.org/nethack/ (+ [tournaments](https://www.hardfought.org/nethack/tournaments/)).
- Cross-server aggregator: https://nethackscoreboard.org.
- Tournaments: https://tnnt.org ([FAQ](https://tnnt.org/faq), [trophies](https://tnnt.org/trophies); game source https://github.com/tnnt-devteam/tnnt), https://junethack.net ([trophies](https://junethack.net/trophies)), devnull history: https://nethack.fandom.com/wiki//dev/null/nethack_tournament, speedrun.com/nethack.

**Bots & automation lineage**
- AutoAscend (NeurIPS 2021 winner, NetHackers' substrate): https://github.com/maciej-sypetkowski/autoascend.
- BotHack (first bot ascension, 2015): https://github.com/krajj7/BotHack; TAEB: https://taeb.github.io; Saiph: https://github.com/canidae/saiph; Interhack (human-harness proxy): https://taeb.github.io/interhack/; wiki overview: https://nethackwiki.com/wiki/Bot.

**Research / evaluation**
- NLE: https://github.com/facebookresearch/nle (archived; new home https://github.com/heiner/nle; paper https://arxiv.org/abs/2006.13760).
- NeurIPS 2021 Challenge report: https://nethackchallenge.com/report.html; Insights paper: https://arxiv.org/abs/2203.11889 (PMLR v176).
- NetHack is Hard to Hack (AutoAscend dissection, HiHack dataset): https://arxiv.org/abs/2305.19240.
- Dungeons & Data (NLD-NAO / NLD-AA): https://arxiv.org/abs/2211.00539.
- BALROG (progression metric): https://arxiv.org/abs/2411.13543, https://balrog-ai.github.io.
- NetPlay (LLM zero-shot agent): https://arxiv.org/abs/2403.00690; LuckyMera (modular hybrid framework): https://arxiv.org/abs/2307.08532.
- Revisiting the NLE (interface critique, ICLR'26 blogpost): https://iclr-blogposts.github.io/2026/blog/2026/revisiting-the-nle/; 2026 LLM-harness status: https://kenforthewin.github.io/blog/posts/nethack-agent/.

---

## 8. Where the community disagrees (handle with care)

1. **Role difficulty rankings** — folklore (Valkyrie easiest, Tourist hardest) vs raw NAO data (Valkyrie 1.7% raw win rate; the wiki itself calls one popular table's Archeologist placement "extremely misplaced"). Consensus resolution: condition on players trying to win (streaks). Use streak-conditioned rates as difficulty priors, never raw popularity-confounded rates.
2. **MR-first vs reflection-first** for the first wish: wiki strategy pages emphasize MR's irreplaceability; codehappy's expert data shows early *reflection* wins more games. Both agree you need both pre-Gehennom.
3. **Protection racket** (Healer early-AC strategy): popular and wiki-endorsed; codehappy's data says it *underperforms* standard play (49.9% vs equivalent-AC baselines) outside conduct runs.
4. **Score as a metric**: officially tracked, community-distrusted ("a triumph in proving that NetHack high scores are meaningless" — pudding-farming era; low-score ascensions prestigious). Tournaments keep both Highest- and Lowest-Scoring Ascension boards as a wink.
5. **Unified leaderboard scoring**: TNNT tried it for 3 years and abandoned it as unfixably biased — multiple leaderboards won. (Junethack still runs a points-based clan aggregate; both coexist.)
6. **Seeded vs unseeded legitimacy**: seeded records are tracked but always segregated from blind records; 3.7's new `reroll` option is currently "no community consensus on legitimacy" ([Conduct](https://nethackwiki.com/wiki/Conduct)).
7. **Gehennom design**: widely called the boring part ("lost more characters to boredom than to deaths"); most variants redesigned it. Irrelevant to bot *motivation*, but it means human late-game spoilers are thinner and more contested than early-game ones.

---

## Appendix A — NAO top-25 causes of death (share of ~8.6M finished games)

jackal 0.83% · dwarf 0.80% · gnome lord 0.67% · soldier ant 0.67% · sewer rat 0.62% · giant bat 0.61% · small mimic 0.59% · gnome 0.56% · fox 0.55% · water moccasin 0.54% · rothe 0.53% · wand 0.53% · slipped while mounting saddled pet 0.50% · giant ant 0.44% · **ascended 0.39%** · hobbit 0.38% · rotted-corpse poisoning 0.37% · water demon 0.37% · shopkeeper 0.35% · goblin 0.34% · hill orc 0.32% · killer bee 0.31% · kitten 0.30% · newt 0.26% · gas spore explosion 0.26%. ([source](https://www.alt.org/nethack/topdeaths.html))

## Appendix B — Milestone ladder (devnull stars ∪ TNNT achievements), suggested progression anchor

Sokoban completed → Mines' End luckstone → (Lesser trophy = both) → Quest nemesis → Medusa → Castle/wand of wishing → enter Gehennom → Vlad/Candelabrum → Wizard of Yendor/Book → invocation → Amulet from High Priest → Elemental Planes → Astral Plane → **Ascension**; cross-game meta: per-identity ascensions (73 combos), Z-score coverage, streaks, conducts, turncount/realtime records.
