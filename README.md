# Options Payoff Grid

Python CLI for sampling expiry payoff across a price range for multi-leg options and stock positions.

## Why this exists

When comparing option structures, a verbal description is not enough. This tool turns a small CSV of legs into a concrete price-to-payoff table you can review, export, and attach to a trade note.

## Supported legs

- `call`
- `put`
- `stock`

Each row also tracks whether the leg is `long` or `short`, the premium or entry price, quantity, and multiplier.

## Input format

```csv
label,kind,side,strike,premium,quantity,multiplier
Long 100 Call,call,long,100,6.50,1,100
Short 110 Call,call,short,110,2.10,1,100
Hedge Shares,stock,long,,98.00,25,1
```

- For `stock` rows, leave `strike` blank.
- For option rows, `premium` is per-share premium.
- For stock rows, `premium` is the entry price.

## Usage

```bash
python options_payoff_grid.py --input sample_legs.csv --price-start 70 --price-end 140 --price-step 5
```

Export the sampled grid and summary:

```bash
python options_payoff_grid.py ^
  --input sample_legs.csv ^
  --price-start 70 ^
  --price-end 140 ^
  --price-step 5 ^
  --output reports/payoff_grid.csv ^
  --summary-json reports/payoff_summary.json
```

## Output

The CLI prints:

- sampled max profit / max loss across the chosen range
- approximate breakevens when the payoff crosses zero
- leg breakdown
- one row per sampled underlying price

## Verification

```bash
python -m py_compile options_payoff_grid.py
python options_payoff_grid.py --input sample_legs.csv --price-start 70 --price-end 140 --price-step 10
```

## Portfolio Positioning

- Project type: Python command-line quant utility
- Best use: fast scenario checks for spreads, collars, and covered structures
- Direction fit: practical standalone software with finance workflow value
