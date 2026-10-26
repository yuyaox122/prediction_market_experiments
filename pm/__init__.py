"""Polymarket first-principles research toolkit.

Data: TimeSeventeen/Polymarket-v1 (CC-BY-4.0), cleaned on-chain OrderFilled tape.
One row = one maker/taker fill. `taker` is the aggressor; `taker_direction` is BUY/SELL
of `asset_id` (an outcome token). usdc_amount is notional; shares = usdc_amount / price.
"""
