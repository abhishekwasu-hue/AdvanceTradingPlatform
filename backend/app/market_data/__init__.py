"""Market data for the autonomous trading worker: broker candles (with a short Redis cache and
resampling up to every timeframe a strategy needs), last-traded prices, and the exchange
session/holiday calendar that decides whether there is anything to trade right now.
"""
