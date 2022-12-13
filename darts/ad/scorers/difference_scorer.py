"""
Difference
-----

Difference distance metric. Returns the difference between each
timestamps of two series. If the two series are multivariate, it
will return a multivariate series.
"""

from darts.ad.scorers.scorers import NonFittableAnomalyScorer
from darts.timeseries import TimeSeries


class DifferenceScorer(NonFittableAnomalyScorer):
    def __init__(self) -> None:
        super().__init__(univariate_scorer=True, window=1)

    def __str__(self):
        return "Difference"

    def _score_core_from_prediction(
        self,
        actual_series: TimeSeries,
        pred_series: TimeSeries,
    ) -> TimeSeries:
        self._assert_deterministic(actual_series, "actual_series")
        self._assert_deterministic(pred_series, "pred_series")
        return actual_series - pred_series
