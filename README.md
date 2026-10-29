# WattOptimizer

**Open-source optimal pacing for time trials and triathlon bike legs.**

Commercial race-pacing tools are closed black boxes. WattOptimizer treats a course
as a trajectory-optimisation problem you can read, audit and extend: rider
mechanics, road gradient, cornering, a spatio-temporal wind field and two
bioenergetic state equations (W′ balance and glycogen) are solved together for the
power profile that **minimises finish time** while arriving at T2 with the reserves
you choose instead of an empty tank.

```text
Course     WattOptimizer demo 40 km TT: 40.0 km, +381 m
Finish     1:00:31  (avg 271 W = 97% CP, 39.7 km/h, 983 kJ)
Reserves   W'bal 5.0 kJ (25%), glycogen 317 g at the finish
Even pace  276 W constant meets the same reserves in 1:02:05 -> plan saves 94 s

         km  grade  target  speed  wind  split  elapsed  Wbal  CHO  pos
-----------------------------------------------------------------------
  8.0- 10.0    4.4     300   20.1   3.0   5:59    18:49   3.5  439  climb
 10.0- 12.0    1.9     285   27.3   3.0   4:24    23:13   1.0  423  mixed
 ...
 30.0- 32.0   -4.4     125   65.6  -3.0   1:50    50:26  12.1  349  aero
```

## Quick start

```bash
git clone https://github.com/fregocap/wattoptimizer.git && cd wattoptimizer
pip install -e ".[fit,plot]"          # or: uv sync --all-extras

wattopt demo-course demo.gpx           # synthetic 40 km out-and-back TT
wattopt plan demo.gpx -c examples/athlete.toml \
    --wind-speed 5 --wind-from 90 --plot plan.png --csv plan.csv
```

With a real course and the forecast for race morning (Open-Meteo, no API key; past
dates use the historical archive so you can re-analyse a race):

```bash
wattopt plan ironman_bike.gpx -c me.toml --weather --start 2026-07-12T07:30+02:00
```

From Python:

```python
from wattoptimizer import Athlete, RaceSpec, ConstantWind, load_course, optimize
from wattoptimizer.report import splits, format_table

course = load_course("course.gpx", ds=100)
plan = optimize(course, Athlete(cp=300, w_prime=22_000),
                RaceSpec(w_prime_final=0.3, glycogen_final=250),
                wind=ConstantWind(speed=4, direction_from=225))
print(format_table(splits(plan)))
```

## The model

Distance $s$ is the independent variable. The course is a curve in $\mathbb{R}^3$,
resampled to $n$ segments of length $L_k$ with grade angle $\theta_k$ and heading
$\psi_k$.

**Mechanics.** For speed $v$, power $P$ and wind $\vec W(x, t)$ projected onto the
direction of travel (head wind $w = -\vec W\cdot\hat\tau$, scaled from 10 m to rider height),

$$
m_\text{eff}\, v \frac{dv}{ds} = \frac{\eta P}{v}
  - \tfrac12 \rho(z)\, C_dA\, (v + w)\,|v + w|
  - m g\,(C_{rr}\cos\theta + \sin\theta) - F_\text{brake}
$$

$C_dA$ switches between the aero and the seated-upright position, and $\rho$ follows the
barometric formula at the course elevation and the race temperature.

**W′ balance** (differential form, Skiba et al. 2015), with $P - CP = e^+ - e^-$ and $e^\pm \ge 0$:

$$
\frac{dW'}{dt} = -e^+ + e^-\,\frac{W'_0 - W'}{W'_0}
$$

**Carbohydrate store.** Metabolic power is $P / \text{GE}$; the fraction supplied by
carbohydrate follows a logistic "crossover" curve in intensity $P/CP$:

$$
\frac{dG}{dt} = I_\text{CHO} - \frac{f_\text{CHO}(P/CP)\; P}{\text{GE}\cdot 16.7\ \text{kJ/g}}
$$

**Optimal control problem.**

$$
\min_{P(\cdot)}\ T \quad\text{s.t.}\quad
\begin{cases}
\text{dynamics above},\quad v(0) = v_\text{start},\ W'(0) = W'_0,\ G(0) = G_0\\
0 \le P \le P_\text{max},\quad W' \ge 0,\quad G \ge 0\\
v \le \min\!\left(v_\text{max},\ \sqrt{a_\text{lat}\, r(s)}\right) \quad\text{(cornering)}\\
W'(S) \ge W'_\text{T2},\quad G(S) \ge G_\text{T2} \quad\text{(arrive with a reserve)}
\end{cases}
$$

### How it is solved

* **Direct collocation** (trapezoidal, in the distance domain) turns the problem into a
  sparse NLP solved by **IPOPT via CasADi**. Each segment satisfies the exact
  work-energy balance $\eta P\,\Delta t - F L = \Delta(\tfrac12 m_\text{eff} v^2)$, and the
  forward simulator uses the *same* discretisation, so `simulate(plan.power)` reproduces
  the optimiser's trajectory (this is checked by the test suite).
* The W′ switch at $P = CP$ is modelled **exactly** by splitting power into the
  non-negative excess $e^+$ and deficit $e^-$ instead of smoothing a `max()`. Smoothing
  puts large curvature right on the singular arc where an optimal TT rides
  ($P = CP$, $W' = 0$) and made IPOPT stall.
* Wind depends on *when* each segment is ridden, and the position on how fast. Both
  are resolved by a short **fixed-point loop**: solve → re-time the wind field →
  re-choose positions → warm-start from a forward simulation → re-solve.
* Plans are compared against the **best feasible even pace**: the highest constant
  power that meets the same reserves under the same wind and corners.

A 40 km course on a 100 m grid solves in a few seconds.

### Why direct collocation rather than Pontryagin?

The problem has state constraints ($W' \ge 0$, corner speed limits) that create boundary
arcs, plus singular arcs at $P = CP$. Indirect methods built on Pontryagin's Maximum
Principle then need the switching structure guessed in advance. Collocation handles
path constraints natively. The NLP multipliers approximate the PMP costates, so the
indirect view is still available for analysis (see Roadmap).

## Configuration

Everything lives in a TOML file with `[athlete]` and `[race]` tables; see
[`examples/athlete.toml`](examples/athlete.toml) and the documented dataclasses in
[`athlete.py`](src/wattoptimizer/athlete.py). Key race settings:

| key | meaning |
|---|---|
| `w_prime_final` | fraction of W′ you must still have at T2 |
| `glycogen_final` | grams of carbohydrate store left at T2 |
| `climb_grade`, `aero_min_speed` | when to sit up (grade above / speed below) |
| `aero_cp_factor` (athlete) | CP on the aero bars relative to upright, which makes sitting up a real trade-off |
| `lateral_accel`, `max_speed` | cornering limit $v^2/r$ and absolute speed cap |
| `wind_height_factor` | rider-height wind / 10 m forecast wind (log-law, ~0.5–0.7) |

## Assumptions and known limitations

Read these before trusting a number on race day.

* **CP/W′ is a two-parameter model.** It says CP is sustainable indefinitely, so the
  **glycogen constraint is what makes long-course (70.3/Ironman) pacing realistic**. Set
  `glycogen_final` deliberately. Durability (CP decay with accumulated work) is not modelled yet.
* **W′bal recovery is fast in this model.** Optimal plans drain W′ to zero before
  every crest and recover on the descents. That matches the model, but real
  repeatability is lower, so consider raising `w_prime_final` or using a smaller W′.
* **The carbohydrate model is coarse**: a logistic CHO fraction, constant gross
  efficiency, no resting metabolism, no gut-absorption dynamics. Calibrate
  `cho_crossover`/`cho_slope` against lab substrate data if you have it.
* **Positions follow rules**, not optimisation (grade/speed thresholds), and are
  resolved in the outer loop.
* **Crosswind yaw is ignored**: only the along-track wind component enters, so $C_dA$
  does not change with yaw.
* **Corners are resolved at the grid spacing** (`--ds`), so hairpins on a 100 m grid
  are only approximate.
* **Finish-line coasting**: the kinetic energy at the line is free, so plans ease off
  in the final 100–200 m. Ignore that bit; for a triathlon it's your dismount anyway.
* Not training or medical advice. Validate against your own power files.

## Roadmap

Contributions are welcome. Good first big items:

- [ ] Optimise the riding position as a relaxed or mixed-integer decision instead of by rule
- [ ] Yaw-dependent $C_dA(\beta)$ using the crosswind component
- [ ] Durability: CP/W′ decaying with accumulated kJ above a threshold
- [ ] Run leg: couple bike reserves to a running power/pace model for full-race optimisation
- [ ] Stochastic wind: optimise expected time over an ensemble forecast
- [ ] Export targets to head units (.fit workout / course points) and Zwift/ERG files
- [ ] Indirect (PMP) solver seeded from the collocation multipliers
- [ ] Parameter estimation of $C_dA$/$C_{rr}$ and CP/W′ from your own .fit rides

## Development

```bash
uv sync --all-extras
uv run pytest
```

Layout: `course.py` (GPX/FIT → resampled geometry) · `physics.py` / `physiology.py`
(state equations, written once for NumPy and CasADi) · `wind.py` (fields + Open-Meteo)
· `optimizer.py` (the NLP) · `simulate.py` (forward model, even-pace baseline) ·
`report.py` / `plot.py` / `cli.py`.

## License

MIT, see [LICENSE](LICENSE).
