from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path
import re


@dataclass
class Leg:
    label: str
    kind: str
    side: str
    strike: float | None
    premium: float
    quantity: float
    multiplier: float

    def payoff(self, underlying_price: float) -> float:
        if self.kind == "call":
            intrinsic = max(0.0, underlying_price - float(self.strike or 0.0))
            unit_payoff = intrinsic - self.premium
        elif self.kind == "put":
            intrinsic = max(0.0, float(self.strike or 0.0) - underlying_price)
            unit_payoff = intrinsic - self.premium
        else:
            unit_payoff = underlying_price - self.premium

        signed = unit_payoff if self.side == "long" else -unit_payoff
        return signed * self.quantity * self.multiplier

    def csv_column(self) -> str:
        normalized = re.sub(r"[^a-z0-9]+", "_", self.label.strip().lower()).strip("_")
        return f"leg_{normalized or 'unnamed'}_payoff"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build an expiry payoff grid for options and stock legs from a CSV."
    )
    parser.add_argument("--input", type=Path, required=True, help="CSV with leg definitions.")
    parser.add_argument("--price-start", type=float, required=True, help="Start underlying price.")
    parser.add_argument("--price-end", type=float, required=True, help="End underlying price.")
    parser.add_argument("--price-step", type=float, default=5.0, help="Price step between rows.")
    parser.add_argument("--output", type=Path, help="Optional CSV path for the payoff grid.")
    parser.add_argument("--summary-json", type=Path, help="Optional JSON path for the scenario summary.")
    parser.add_argument("--markdown-report", type=Path, help="Optional Markdown path for a trade-note style summary.")
    return parser.parse_args()


def parse_float(value: str, row_number: int, field_name: str) -> float:
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"Invalid {field_name} at row {row_number}.") from exc


def load_legs(path: Path) -> list[Leg]:
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"label", "kind", "side", "strike", "premium", "quantity", "multiplier"}
        missing = required.difference(set(reader.fieldnames or []))
        if missing:
            raise ValueError(f"Missing required columns: {', '.join(sorted(missing))}")

        legs: list[Leg] = []
        for row_number, row in enumerate(reader, start=2):
            label = str(row["label"]).strip() or f"leg-{row_number - 1}"
            kind = str(row["kind"]).strip().lower()
            side = str(row["side"]).strip().lower()
            if kind not in {"call", "put", "stock"}:
                raise ValueError(f"Invalid kind at row {row_number}. Use call, put, or stock.")
            if side not in {"long", "short"}:
                raise ValueError(f"Invalid side at row {row_number}. Use long or short.")

            strike_raw = str(row["strike"]).strip()
            strike = None if kind == "stock" and not strike_raw else parse_float(strike_raw or "0", row_number, "strike")
            premium = parse_float(str(row["premium"]).strip(), row_number, "premium")
            quantity = parse_float(str(row["quantity"]).strip(), row_number, "quantity")
            multiplier = parse_float(str(row["multiplier"]).strip(), row_number, "multiplier")

            if kind != "stock" and strike is None:
                raise ValueError(f"strike is required for option legs at row {row_number}.")
            if quantity <= 0:
                raise ValueError(f"quantity must be positive at row {row_number}.")
            if multiplier <= 0:
                raise ValueError(f"multiplier must be positive at row {row_number}.")

            legs.append(
                Leg(
                    label=label,
                    kind=kind,
                    side=side,
                    strike=strike,
                    premium=premium,
                    quantity=quantity,
                    multiplier=multiplier,
                )
            )

    if not legs:
        raise ValueError("No option legs found in the CSV.")
    return legs


def build_price_grid(start: float, end: float, step: float) -> list[float]:
    if step <= 0:
        raise ValueError("price-step must be positive.")
    if end < start:
        raise ValueError("price-end must be greater than or equal to price-start.")

    prices: list[float] = []
    current = start
    while current <= end + 1e-9:
        prices.append(round(current, 4))
        current += step
    return prices


def interpolate_zero_crossing(left_price: float, left_payoff: float, right_price: float, right_payoff: float) -> float:
    if right_price == left_price or left_payoff == right_payoff:
        return round(left_price, 2)
    ratio = abs(left_payoff) / (abs(left_payoff) + abs(right_payoff))
    return round(left_price + ((right_price - left_price) * ratio), 2)


def build_profit_zones(rows: list[dict[str, float]], start: float, end: float) -> list[dict[str, float]]:
    profit_zones: list[dict[str, float]] = []
    zone_start: float | None = start if rows and rows[0]["net_payoff"] > 0 else None

    for previous, current in zip(rows, rows[1:]):
        prev_payoff = previous["net_payoff"]
        curr_payoff = current["net_payoff"]
        if prev_payoff == curr_payoff:
            continue

        if prev_payoff <= 0 < curr_payoff:
            zone_start = interpolate_zero_crossing(
                previous["underlying_price"],
                prev_payoff,
                current["underlying_price"],
                curr_payoff,
            )
        elif prev_payoff > 0 >= curr_payoff and zone_start is not None:
            zone_end = interpolate_zero_crossing(
                previous["underlying_price"],
                prev_payoff,
                current["underlying_price"],
                curr_payoff,
            )
            profit_zones.append({"start": round(zone_start, 2), "end": round(zone_end, 2)})
            zone_start = None

    if zone_start is not None:
        profit_zones.append({"start": round(zone_start, 2), "end": round(end, 2)})

    return profit_zones


def format_profit_zones(profit_zones: list[dict[str, float]]) -> str:
    if not profit_zones:
        return "none inside sampled range"
    return ", ".join(f"${zone['start']:.2f} to ${zone['end']:.2f}" for zone in profit_zones)


def summarize_grid(rows: list[dict[str, float]], start: float, end: float) -> dict[str, object]:
    profits = [row["net_payoff"] for row in rows]
    max_profit = max(profits)
    max_loss = min(profits)
    max_profit_price = next(row["underlying_price"] for row in rows if row["net_payoff"] == max_profit)
    max_loss_price = next(row["underlying_price"] for row in rows if row["net_payoff"] == max_loss)

    breakevens: list[float] = []
    for previous, current in zip(rows, rows[1:]):
        prev_payoff = previous["net_payoff"]
        curr_payoff = current["net_payoff"]
        if prev_payoff == 0:
            breakevens.append(previous["underlying_price"])
        elif prev_payoff * curr_payoff < 0:
            span = current["underlying_price"] - previous["underlying_price"]
            if span == 0:
                breakevens.append(previous["underlying_price"])
            else:
                ratio = abs(prev_payoff) / (abs(prev_payoff) + abs(curr_payoff))
                breakevens.append(round(previous["underlying_price"] + (span * ratio), 2))

    if rows[0]["net_payoff"] < rows[-1]["net_payoff"] and max_profit_price == end:
        profit_label = "unbounded upside beyond sampled range"
    else:
        profit_label = f"sampled max at {max_profit_price:.2f}"

    if rows[0]["net_payoff"] > rows[-1]["net_payoff"] and max_loss_price == end:
        loss_label = "loss worsens above sampled range"
    elif rows[0]["net_payoff"] < rows[-1]["net_payoff"] and max_loss_price == start:
        loss_label = "loss worsens below sampled range"
    else:
        loss_label = f"sampled min at {max_loss_price:.2f}"

    profit_zones = build_profit_zones(rows, start, end)

    return {
        "sampled_price_start": start,
        "sampled_price_end": end,
        "row_count": len(rows),
        "max_profit": round(max_profit, 2),
        "max_profit_note": profit_label,
        "max_loss": round(max_loss, 2),
        "max_loss_note": loss_label,
        "breakevens": breakevens,
        "profit_zones": profit_zones,
    }


def print_report(legs: list[Leg], rows: list[dict[str, float]], summary: dict[str, object]) -> None:
    print("Options Payoff Grid")
    print("===================")
    print(f"Legs loaded:             {len(legs)}")
    print(f"Sampled price range:     {summary['sampled_price_start']:.2f} -> {summary['sampled_price_end']:.2f}")
    print(f"Sampled max profit:      ${summary['max_profit']:.2f} ({summary['max_profit_note']})")
    print(f"Sampled max loss:        ${summary['max_loss']:.2f} ({summary['max_loss_note']})")
    breakevens = summary["breakevens"]
    print(f"Approx breakevens:       {', '.join(f'${value:.2f}' for value in breakevens) if breakevens else 'none inside sampled range'}")
    print(f"Profit zones:            {format_profit_zones(summary['profit_zones'])}")
    print()

    print("Leg breakdown:")
    for leg in legs:
        strike_text = "-" if leg.kind == "stock" else f"{float(leg.strike or 0.0):.2f}"
        print(
            f"  {leg.label:<18} {leg.side:<5} {leg.kind:<5} "
            f"strike {strike_text:<7} premium {leg.premium:<7.2f} qty {leg.quantity:<5.2f} x {leg.multiplier:.0f}"
        )

    print()
    print(f"{'Underlying':>11} {'Net Payoff':>12}")
    print("-" * 25)
    for row in rows:
        print(f"{row['underlying_price']:>11.2f} {row['net_payoff']:>12.2f}")


def build_rows(legs: list[Leg], prices: list[float]) -> list[dict[str, float]]:
    rows: list[dict[str, float]] = []
    for price in prices:
        row: dict[str, float] = {"underlying_price": price}
        total = 0.0
        for leg in legs:
            leg_payoff = round(leg.payoff(price), 2)
            row[leg.csv_column()] = leg_payoff
            total += leg_payoff
        row["net_payoff"] = round(total, 2)
        rows.append(row)
    return rows


def write_grid(path: Path, rows: list[dict[str, float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_markdown_report(path: Path, legs: list[Leg], summary: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Options Payoff Brief",
        "",
        f"- Sampled range: `${summary['sampled_price_start']:.2f}` to `${summary['sampled_price_end']:.2f}`",
        f"- Sampled max profit: `${summary['max_profit']:.2f}` ({summary['max_profit_note']})",
        f"- Sampled max loss: `${summary['max_loss']:.2f}` ({summary['max_loss_note']})",
        f"- Approx breakevens: {', '.join(f'${value:.2f}' for value in summary['breakevens']) if summary['breakevens'] else 'none inside sampled range'}",
        f"- Profit zones: {format_profit_zones(summary['profit_zones'])}",
        "",
        "## Legs",
    ]
    for leg in legs:
        strike_text = "-" if leg.kind == "stock" else f"{float(leg.strike or 0.0):.2f}"
        lines.append(
            f"- `{leg.label}` | {leg.side} {leg.kind} | strike `{strike_text}` | premium `{leg.premium:.2f}` | qty `{leg.quantity:.2f}` | multiplier `{leg.multiplier:.0f}`"
        )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    legs = load_legs(args.input)
    prices = build_price_grid(args.price_start, args.price_end, args.price_step)
    rows = build_rows(legs, prices)
    summary = summarize_grid(rows, args.price_start, args.price_end)
    print_report(legs, rows, summary)

    if args.output:
        write_grid(args.output, rows)
        print(f"\nWrote payoff grid: {args.output}")

    if args.summary_json:
        args.summary_json.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "legs": [leg.__dict__ for leg in legs],
            "summary": summary,
        }
        args.summary_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"Wrote summary JSON: {args.summary_json}")

    if args.markdown_report:
        write_markdown_report(args.markdown_report, legs, summary)
        print(f"Wrote Markdown report: {args.markdown_report}")


if __name__ == "__main__":
    main()
