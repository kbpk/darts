"""
Croston method
--------------
"""

from typing import Optional

from statsforecast.models import TSB as CrostonTSB
from statsforecast.models import CrostonClassic, CrostonOptimized, CrostonSBA

from darts.logging import raise_if, raise_if_not
from darts.models.forecasting.forecasting_model import DualCovariatesForecastingModel
from darts.timeseries import TimeSeries


class Croston(DualCovariatesForecastingModel):
    def __init__(
        self, version: str = "classic", alpha_d: float = None, alpha_p: float = None
    ):
        """An implementation of the `Croston method
        <https://otexts.com/fpp3/counts.html>`_ for intermittent
        count series.

        Relying on the implementation of `Statsforecasts package
        <https://github.com/Nixtla/statsforecast>`_.

        Parameters
        ----------
        version
            - "classic" corresponds to classic Croston.
            - "optimized" corresponds to optimized classic Croston, which searches
              for the optimal ``alpha`` smoothing parameter and can take longer
              to run. Otherwise, a fixed value of ``alpha=0.1`` is used.
            - "sba" corresponds to the adjustment of the Croston method known as
              the Syntetos-Boylan Approximation [1]_.
            - "tsb" corresponds to the adjustment of the Croston method proposed by
              Teunter, Syntetos and Babai [2]_. In this case, `alpha_d` and `alpha_p` must
              be set.
        alpha_d
            For the "tsb" version, the alpha smoothing parameter to apply on demand.
        alpha_p
            For the "tsb" version, the alpha smoothing parameter to apply on probability.

        References
        ----------
        .. [1] Aris A. Syntetos and John E. Boylan. The accuracy of intermittent demand estimates.
               International Journal of Forecasting, 21(2):303 – 314, 2005.
        .. [2] Ruud H. Teunter, Aris A. Syntetos, and M. Zied Babai.
               Intermittent demand: Linking forecasting to inventory obsolescence.
               European Journal of Operational Research, 214(3):606 – 615, 2011.
        """
        super().__init__()
        raise_if_not(
            version.lower() in ["classic", "optimized", "sba", "tsb"],
            'The provided "version" parameter must be set to "classic", "optimized", "sba" or "tsb".',
        )

        if version == "classic":
            self.model = CrostonClassic()
        elif version == "optimized":
            self.model = CrostonOptimized()
        elif version == "sba":
            self.model = CrostonSBA()
        else:
            raise_if(
                alpha_d is None or alpha_p is None,
                'alpha_d and alpha_p must be specified when using "tsb".',
            )
            self.alpha_d = alpha_d
            self.alpha_p = alpha_p
            self.model = CrostonTSB(alpha_d=self.alpha_d, alpha_p=self.alpha_p)

        self.version = version

    def __str__(self):
        return "Croston"

    def _fit(self, series: TimeSeries, future_covariates: Optional[TimeSeries] = None):
        super()._fit(series, future_covariates)
        series._assert_univariate()
        series = self.training_series

        self.model.fit(
            y=series.values(copy=False).flatten(),
            X=future_covariates.values(copy=False).flatten()
            if future_covariates is not None
            else None,
        )

        return self

    def _predict(
        self,
        n: int,
        future_covariates: Optional[TimeSeries] = None,
        num_samples: int = 1,
    ):
        super()._predict(n, future_covariates, num_samples)
        values = self.model.predict(
            h=n,
            X=future_covariates.values(copy=False).flatten()
            if future_covariates is not None
            else None,
        )["mean"]
        return self._build_forecast_series(values)

    @property
    def min_train_series_length(self) -> int:
        return 10

    def _supports_range_index(self) -> bool:
        return True

    def _is_probabilistic(self) -> bool:
        return False
