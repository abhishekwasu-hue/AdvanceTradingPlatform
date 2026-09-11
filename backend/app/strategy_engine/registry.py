from typing import Dict, List

from app.strategy_engine.base import BaseStrategy
from app.strategy_engine.indicator_strategies import build_indicator_strategies
from app.strategy_engine.mtf_strategy import build_mtf_strategies


class StrategyRegistry:
    """Holds every inbuilt auto-executable strategy available on the dashboard."""

    def __init__(self) -> None:
        self._strategies: Dict[str, BaseStrategy] = {}
        for strategy in build_mtf_strategies() + build_indicator_strategies():
            self.register(strategy)

    def register(self, strategy: BaseStrategy) -> None:
        self._strategies[strategy.id] = strategy

    def get(self, strategy_id: str) -> BaseStrategy:
        if strategy_id not in self._strategies:
            raise KeyError(f"Unknown strategy id: {strategy_id}")
        return self._strategies[strategy_id]

    def list_all(self) -> List[BaseStrategy]:
        return list(self._strategies.values())

    def ids(self) -> List[str]:
        return list(self._strategies.keys())


registry = StrategyRegistry()
