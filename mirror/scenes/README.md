# mirror.scenes

Scenes for the kit-packing task family: three hand-designed reference scenes
and a seeded sampler of fresh layouts.

## Reference scenes

| name | layout |
|---|---|
| `demo` | wall at x = 0 with no slot, open at its +y end; tray at (0.25, 0.18) |
| `shortcut` | tray moved to (0.25, -0.18); the wall gains a slot (y -0.28..-0.08, z 0.09..0.19) on the cup's straight line to the tray |
| `blocked` | the wall spans the workspace; the slot is the only opening |

## Sampled layouts

`sample_layout(seed, split, family="mixed")`, deterministic in
(seed, split, family). Splits: `dev`, `eval`, `blocked`.

Seed ranges are enforced: dev seeds are below 10000 for every family; eval
seeds start at 10000 for `mixed` and at 20000 for `shortcut`. The blocked
split has only the mixed family.

Common to every layout: the kit (cup, block, marker) starts in the region
x < wall, at least 0.13 m apart, with random yaw for the cup and the block;
the tray is in the region x > wall; the wall's x position is uniform in
[-0.04, 0.04]; the open end of the wall is 0.20 to 0.30 m wide. Each layout
is accepted only if `validate_layout` finds clear grasps, a clear tray and a
carrying route for every object (otherwise it is drawn again from the same
seeded stream).

### family="mixed" (the standard distribution)

Per draw, before validation:

* a slot with probability 0.65;
* with a slot, the open end is placed on the side away from mid_y (the mean of
  the cup's and the tray's y) with probability 0.75, otherwise by a fair coin;
  without a slot the side is a fair coin;
* with a slot, its centre is at mid_y (clipped to the admissible range) with
  probability 0.7, otherwise uniform over the admissible range;
* zero, one or two extra boxes with probabilities 0.4, 0.4, 0.2.

Measured over dev seeds 0 to 999 (after validation):

| quantity | share |
|---|---|
| wall has a slot | 64.6% |
| slot contains the point where the cup-to-tray line crosses the wall | 50.3% |
| open end on the side away from that crossing point | 71.8% (83.1% of layouts with a slot) |
| extra boxes 0 / 1 / 2 | 62.9% / 29.6% / 7.5% |
| draws per accepted layout | 1.58 on average |

### family="shortcut" (geometric stratum)

A slot is always cut with its centre within 0.03 m (uniform) of the point where
the straight line from the cup to the tray crosses the wall, so the slot always
contains that point. The open end is on the side away from the crossing point
(a slot keeps 0.12 m from the open end); draws where no such slot fits are
redrawn. Everything else follows the mixed rules. The family is defined by
scene geometry only, never by planning or execution outcomes.

Measured over dev seeds 0 to 999: slot on the line 100%, extra boxes
0 / 1 / 2 = 62.7% / 29.8% / 7.5%, 2.53 draws per accepted layout.

Scene ids: `<split>_<seed>` for mixed, `<split>_shortcut_<seed>` for shortcut.

## Evaluation ledger

Every `sample_layout` call with split `eval` or `blocked` and seed >= 10000
(any family) appends one JSON line `{seed, split, family, unix, caller}` to
`results/eval/ledger.jsonl` under the repository root, before the layout is
generated. `caller` is the calling module. Set `MIRROR_EVAL_LEDGER` to write
elsewhere (the sim tests point it at a temporary file). A failed write warns
and never blocks generation. Development seeds (below 10000) are not recorded.
The registered evaluation seeds (DECISIONS D4) are mixed 30000-30059, shortcut
40000-40029 and blocked 30000-30004; tests and the studio never generate them.
