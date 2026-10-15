"""Model how much of a period's downloads is billable.

Every input is required and has no default, because the egress allowance and
the rate beyond it change and depend on the account. Read both from the
Backblaze pricing page and pass them in:

    https://www.backblaze.com/cloud-storage/pricing

Downloads to partner compute providers and CDNs are not counted against the
allowance (see docs/b2-capabilities.md), so pass only the volume that leaves B2
for other destinations as `reads`. The model is a planning aid; it does not
read your account or bill.

    python egress_model.py --volume-gb VOLUME --reads READS --allowance-gb ALLOWANCE [--price-per-gb PRICE]
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass

PRICING_URL = "https://www.backblaze.com/cloud-storage/pricing"


@dataclass(frozen=True)
class EgressEstimate:
    downloaded_gb: float
    allowance_gb: float
    billable_gb: float
    source: str = PRICING_URL


def _non_negative(name: str, value: float) -> float:
    if value < 0:
        raise ValueError(f"{name} must not be negative, got {value}")
    return float(value)


def estimate_egress(volume_gb: float, reads: float, allowance_gb: float) -> EgressEstimate:
    """Downloaded and billable gigabytes for a period.

    volume_gb: size of the data read each time (for example a dataset release).
    reads: how many times that volume is downloaded in the period.
    allowance_gb: gigabytes of egress included for the period, from the pricing page.
    """
    volume = _non_negative("volume_gb", volume_gb)
    count = _non_negative("reads", reads)
    allowance = _non_negative("allowance_gb", allowance_gb)
    downloaded = volume * count
    return EgressEstimate(downloaded, allowance, max(0.0, downloaded - allowance))


def estimate_cost(estimate: EgressEstimate, price_per_gb: float) -> float:
    """Cost of the billable volume at a rate you read from the pricing page."""
    return estimate.billable_gb * _non_negative("price_per_gb", price_per_gb)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], epilog=f"Source for rates and allowance: {PRICING_URL}")
    parser.add_argument("--volume-gb", type=float, required=True, help="gigabytes read each time")
    parser.add_argument("--reads", type=float, required=True, help="times the volume is downloaded in the period")
    parser.add_argument("--allowance-gb", type=float, required=True, help="included egress for the period, from the pricing page")
    parser.add_argument("--price-per-gb", type=float, help="rate beyond the allowance, from the pricing page")
    args = parser.parse_args(argv)
    estimate = estimate_egress(args.volume_gb, args.reads, args.allowance_gb)
    print(f"downloaded_gb={estimate.downloaded_gb:g} allowance_gb={estimate.allowance_gb:g} billable_gb={estimate.billable_gb:g}")
    if args.price_per_gb is not None:
        print(f"cost={estimate_cost(estimate, args.price_per_gb):.2f}")
    print(f"source: {estimate.source}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
