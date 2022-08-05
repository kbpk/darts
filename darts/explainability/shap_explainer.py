"""
Shap-based ForecastingModelExplainer
------------------------------
This class is meant to wrap a shap explainer (https://github.com/slundberg/shap) specifically for time series.

Warning

This is only a shap value of direct influence and doesn't take into account relationships
between past lags themselves. Hence a given past lag could also have an indirect influence via the
intermediate past lags elements between it and the time step we want to explain, if we assume that
the intermediate past lags are generated by the same model.

TODO
    - Optional De-trend  if the timeseries is not stationary.
    There would be 1) a stationarity test and 2) a de-trend methodology for the target. It can be for
    example target - moving_average(input_chunk_length).

"""

from enum import Enum
from typing import Dict, NewType, Optional, Sequence, Union

import matplotlib.pyplot as plt
import pandas as pd
import shap
from numpy import integer
from sklearn.multioutput import MultiOutputRegressor

from darts import TimeSeries
from darts.explainability.explainability import ForecastingModelExplainer
from darts.logging import get_logger, raise_if, raise_log
from darts.models.forecasting.forecasting_model import (
    ForecastingModel,
    GlobalForecastingModel,
)
from darts.models.forecasting.regression_model import RegressionModel

logger = get_logger(__name__)


class _ShapMethod(Enum):
    TREE = 0
    GRADIENT = 1
    DEEP = 2
    KERNEL = 3
    SAMPLING = 4
    PARTITION = 5
    LINEAR = 6
    PERMUTATION = 7
    ADDITIVE = 8


ShapMethod = NewType("ShapMethod", _ShapMethod)

default_sklearn_shap_explainers = {
    "AdaBoostRegressor": _ShapMethod.PERMUTATION,
    "BaggingRegressor": _ShapMethod.PERMUTATION,
    "ExtraTreesRegressor": _ShapMethod.TREE,
    "GradientBoostingRegressor": _ShapMethod.TREE,
    "LGBMRegressor": _ShapMethod.TREE,
    "RandomForestRegressor": _ShapMethod.TREE,
    "LinearRegression": _ShapMethod.LINEAR,
    "RidgeCV": _ShapMethod.PERMUTATION,
}


class ShapExplainer(ForecastingModelExplainer):
    def __init__(
        self,
        model: ForecastingModel,
        background_series: Optional[Union[TimeSeries, Sequence[TimeSeries]]] = None,
        background_past_covariates: Optional[
            Union[TimeSeries, Sequence[TimeSeries]]
        ] = None,
        background_future_covariates: Optional[
            Union[TimeSeries, Sequence[TimeSeries]]
        ] = None,
        shap_method: Optional[str] = None,
        **kwargs,
    ):
        """ShapExplainer

        Nomenclature:
        - A background time series is a time series with which we train the Explainer model.
        - A foreground time series is the time series we will explain according to the fitted Explainer model.

        Parameters
        ----------
        model
            A ForecastingModel we want to explain. It has to be fitted first. Only RegressionModel type for now.
        background_series
            Optionally, a TimeSeries or a list of time series we want to use to compare with any foreground we want
            to explain.
            This is optional, for 2 reasons:
                - In general we want to keep the training_series of the model and this is the default one,
                but in case of multiple time series training (global or meta learning) the ForecastingModel doesn't
                save them. In this case we need to feed a background time series.
                - We might want to consider a reduced well chosen background in order to reduce computation
                time.
        background_past_covariates
            Optionally, a past covariates TimeSeries or list of TimeSeries that the model needs once fitted.
        background_future_covariates
            Optionally, a future covariates TimeSeries or list of TimeSeries that the model needs once fitted.
        shap_method
            Optionally, a shap method we want to apply. By default, the method is chosen automatically with an
            internal mapping.
            Supported values : “permutation”, “partition”, “tree”, “kernel”, “sampling”, “linear”, “deep”, “gradient”
        **kwargs
            Optionally, an additional keyword arguments passed to the shap_method chosen, if any.
        """
        if not issubclass(type(model), RegressionModel):
            raise_log(
                ValueError(
                    "Invalid model type. For now, only RegressionModel type can be explained."
                ),
                logger,
            )

        super().__init__(
            model,
            background_series,
            background_past_covariates,
            background_future_covariates,
        )

        # As we only use RegressionModel, we fix the forecast n step ahead we want to explain as
        # output_chunk_length
        self.n = self.model.output_chunk_length

        if shap_method is not None:
            shap_method = shap_method.upper()
            if shap_method in _ShapMethod.__members__:
                self.shap_method = _ShapMethod[shap_method]
            else:
                raise_log(
                    ValueError(
                        "Invalid shap method. Please choose one value among the following: [partition, tree, "
                        "kernel, sampling, linear, deep, gradient]."
                    )
                )
        else:
            self.shap_method = None

        self.explainers = _RegressionShapExplainers(
            self.model,
            self.background_series,
            self.n,
            self.background_past_covariates,
            self.background_future_covariates,
            shap_method=self.shap_method,
            **kwargs,
        )

    def explain(
        self,
        foreground_series: Optional[Union[TimeSeries, Sequence[TimeSeries]]] = None,
        foreground_past_covariates: Optional[
            Union[TimeSeries, Sequence[TimeSeries]]
        ] = None,
        foreground_future_covariates: Optional[
            Union[TimeSeries, Sequence[TimeSeries]]
        ] = None,
    ) -> Union[
        Dict[integer, Dict[str, TimeSeries]],
        Sequence[Dict[integer, Dict[str, TimeSeries]]],
    ]:
        super().explain(
            foreground_series, foreground_past_covariates, foreground_future_covariates
        )

        if foreground_series is None:
            foreground_series = self.background_series
            foreground_past_covariates = self.background_past_covariates
            foreground_future_covariates = self.background_future_covariates

        if isinstance(foreground_series, TimeSeries):
            foreground_series = [foreground_series]
            foreground_past_covariates = (
                [foreground_past_covariates] if foreground_past_covariates else None
            )
            foreground_future_covariates = (
                [foreground_future_covariates] if foreground_future_covariates else None
            )

        shap_ = self.explainers.shap_explanations(
            foreground_series,
            foreground_past_covariates,
            foreground_future_covariates,
        )

        shap_values_dict = {}
        for h in range(self.n):
            tmp = {}
            for idx, t in enumerate(self.target_names):

                tmp[t] = TimeSeries.from_times_and_values(
                    shap_[h][idx].time_index,
                    shap_[h][idx].values,
                    columns=shap_[h][idx].feature_names,
                )
            shap_values_dict[h] = tmp

        return shap_values_dict

    def summary_plot(
        self,
        target_names: Optional[Sequence[str]] = None,
        horizons: Optional[Sequence[int]] = None,
        nb_samples: Optional[int] = None,
        plot_type: Optional[str] = "dot",
    ):
        """
        Display a shap plot summary per target and per horizon.
        We here reuse the background data as foreground (potentially sampled) to give a general importance
        plot for each feature.
        If no target names and/or no horizons are provided, we plot all summary plots.

        Parameters
        ----------
        target_names
            Optionally, A list of string naming the target names we want to plot.
        horizons
            Optionally, a list of integer values representing which elements in the future
            we want to explain, starting from the first timestamp prediction at 0.
            For now we consider only models with output_chunk_length and it can't be bigger than output_chunk_length.
        nb_samples
            Optionally, an integer value sampling the foreground series (based on the backgound),
            for the sake of performance.
        plot_type
            Optionally, string value for the type of plot proposed by shap library. Currently,
            the following are available: 'dot', 'bar', 'violin'.

        """

        if target_names is not None:
            raise_if(
                any(
                    [
                        target_name not in self.target_names
                        for target_name in target_names
                    ]
                ),
                "One of the target names doesn't exist in the original background ts.",
            )

        if horizons is not None:
            # We suppose for now the output_chunk_length existence
            raise_if(
                max(horizons) > self.n - 1,
                "One of the horizons is greater than the model output_chunk_length.",
            )

        if nb_samples:
            foreground_X_sampled = shap.utils.sample(
                self.explainers.background_X, nb_samples
            )
        else:
            foreground_X_sampled = self.explainers.background_X

        # shap_values = []
        if target_names is None:
            target_names = self.target_names
        if horizons is None:
            horizons = range(self.model.output_chunk_length)

        shaps_ = self.explainers.shap_explanations(
            self.background_series,
            self.background_past_covariates,
            self.background_future_covariates,
            nb_samples,
        )
        for idx, t in enumerate(target_names):
            for h in horizons:
                plt.title("Target: `{}` - Horizon: {}".format(t, "t+" + str(h)))
                shap.summary_plot(
                    shaps_[h][idx], foreground_X_sampled, plot_type=plot_type
                )


class _RegressionShapExplainers:
    """
    Helper Class to wrap the different cases we encounter with shap different explainers, multivariates,
    horizon etc.
    Aim to provide shap values for any type of RegressionModel. Manage the MultioutputRegressor cases.
    For darts RegressionModel only.
    """

    def __init__(
        self,
        model: GlobalForecastingModel,
        background_series: Union[TimeSeries, Sequence[TimeSeries]],
        n: integer,
        background_past_covariates: Optional[
            Union[TimeSeries, Sequence[TimeSeries]]
        ] = None,
        background_future_covariates: Optional[
            Union[TimeSeries, Sequence[TimeSeries]]
        ] = None,
        shap_method: Optional[ShapMethod] = None,
        background_nb_samples: Optional[int] = None,
        **kwargs,
    ):

        self.model = model
        self.is_multiOutputRegressor = isinstance(
            self.model.model, MultiOutputRegressor
        )
        self.target_dim = self.model.input_dim["target"]
        self.n = n

        self.single_output = False
        if (self.n == 1) and (self.target_dim) == 1:
            self.single_output = True

        self.background_X = self._create_regression_model_shap_X(
            background_series,
            background_past_covariates,
            background_future_covariates,
            background_nb_samples,
        )

        # print(self.is_multiOutputRegressor)
        if self.is_multiOutputRegressor:
            self.explainers = {}
            for i in range(self.n):
                self.explainers[i] = {}
                for j in range(self.target_dim):
                    self.explainers[i][j] = self._get_explainer_sklearn(
                        self.model.model.estimators_[i + j],
                        self.background_X,
                        shap_method,
                        **kwargs,
                    )
        else:
            self.explainers = self._get_explainer_sklearn(
                self.model.model, self.background_X, shap_method, **kwargs
            )

    def shap_explanations(
        self,
        foreground_series: TimeSeries,
        foreground_past_covariates: Optional[TimeSeries] = None,
        foreground_future_covariates: Optional[TimeSeries] = None,
        n_samples: Optional[integer] = None,
    ) -> Dict:

        foreground_X = self._create_regression_model_shap_X(
            foreground_series,
            foreground_past_covariates,
            foreground_future_covariates,
            n_samples,
        )

        # Creation of an unified dictionary between multiOutputRegressor and native
        shap_explanations = {}
        if self.is_multiOutputRegressor:

            for i in range(self.n):
                tmp_n = {}
                for j in range(self.target_dim):
                    explainer = self.explainers[i][j](foreground_X)
                    explainer.base_values = explainer.base_values.ravel()
                    explainer.time_index = foreground_X.index
                    tmp_n[j] = explainer
                shap_explanations[i] = tmp_n
        else:
            shap_explanation_tmp = self.explainers(foreground_X)
            for i in range(self.n):
                tmp_n = {}
                for j in range(self.target_dim):
                    # If we don't use shap._explanation.Explanation native private class, it is impossible
                    # to use shap plot functions later.
                    if self.single_output is False:
                        tmp_t = shap._explanation.Explanation(
                            shap_explanation_tmp.values[:, :, self.target_dim * i + j]
                        )
                        tmp_t.base_values = shap_explanation_tmp.base_values[
                            :, self.target_dim * i + j
                        ].ravel()
                    else:
                        tmp_t = shap_explanation_tmp
                        tmp_t.base_values = shap_explanation_tmp.base_values.ravel()

                    tmp_t.feature_names = shap_explanation_tmp.feature_names
                    tmp_t.time_index = foreground_X.index
                    tmp_n[j] = tmp_t
                shap_explanations[i] = tmp_n

        return shap_explanations

    def _get_explainer_sklearn(
        self,
        model_sklearn,
        background_X: pd.DataFrame,
        shap_method: Optional[ShapMethod] = None,
        **kwargs,
    ):

        model_name = type(model_sklearn).__name__

        if shap_method is None:
            if model_name in default_sklearn_shap_explainers.keys():
                shap_method = default_sklearn_shap_explainers[model_name]
            else:
                shap_method = _ShapMethod.KERNEL

        if shap_method == _ShapMethod.TREE:
            if "feature_perturbation" in kwargs:
                if kwargs.get("feature_perturbation") == "interventional":
                    explainer = shap.TreeExplainer(
                        model_sklearn, background_X, **kwargs
                    )
                else:
                    explainer = shap.TreeExplainer(model_sklearn, **kwargs)
            else:
                explainer = shap.TreeExplainer(model_sklearn, **kwargs)
        elif shap_method == _ShapMethod.PERMUTATION:
            explainer = shap.PermutationExplainer(
                model_sklearn.predict, background_X, **kwargs
            )
        elif shap_method == _ShapMethod.KERNEL:
            explainer = shap.KernelExplainer(
                model_sklearn.predict, background_X, keep_index=True
            )
        elif shap_method == _ShapMethod.LINEAR:
            explainer = shap.LinearExplainer(model_sklearn, background_X, **kwargs)

        logger.info("The shap method used is of type: " + str(type(explainer)))

        return explainer

    def _create_regression_model_shap_X(
        self, target_series, past_covariates, future_covariates, n_samples=None
    ):
        """
        Helper function that creates training/validation matrices (X and y as required in sklearn), given series and
        max_samples_per_ts.

        Partially adapted from _create_lagged_data funtion in regression_model

        X has the following structure:
        lags_target | lags_past_covariates | lags_future_covariates

        Where each lags_X has the following structure (lags_X=[-2,-1] and X has 2 components):
        lag_-2_comp_1_X | lag_-2_comp_2_X | lag_-1_comp_1_X | lag_-1_comp_2_X

        y has the following structure (output_chunk_length=4 and target has 2 components):
        lag_+0_comp_1_target | lag_+0_comp_2_target | ... | lag_+3_comp_1_target | lag_+3_comp_2_target
        """

        # ensure list of TimeSeries format
        if isinstance(target_series, TimeSeries):
            target_series = [target_series]
            past_covariates = [past_covariates] if past_covariates else None
            future_covariates = [future_covariates] if future_covariates else None

        Xs = []
        # iterate over series
        for idx, target_ts in enumerate(target_series):
            covariates = [
                (
                    past_covariates[idx].pd_dataframe(copy=False)
                    if past_covariates
                    else None,
                    self.model.lags.get("past"),
                ),
                (
                    future_covariates[idx].pd_dataframe(copy=False)
                    if future_covariates
                    else None,
                    self.model.lags.get("future"),
                ),
            ]

            df_X = []
            df_target = target_ts.pd_dataframe(copy=False)

            # X: target lags
            if "target" in self.model.lags:
                for lag in self.model.lags["target"]:
                    self.model.lags["target"]
                    df_tmp = df_target.shift(-lag)
                    df_X.append(
                        df_tmp.rename(
                            columns={
                                c: c + "_target_lag" + str(lag) for c in df_tmp.columns
                            }
                        )
                    )

            # X: covariate lags
            for idx, (df_cov, lags) in enumerate(covariates):
                if lags:
                    for lag in lags:
                        df_tmp = df_cov.shift(-lag)
                        if idx == 0:
                            cov_type = "past"
                        else:
                            cov_type = "fut"
                        df_X.append(
                            df_tmp.rename(
                                columns={
                                    c: c + "_" + cov_type + "_cov_lag" + str(lag)
                                    for c in df_tmp.columns
                                }
                            )
                        )

            # combine lags
            Xs.append(pd.concat(df_X, axis=1).dropna())

        # combine samples from all series
        X = pd.concat(Xs, axis=0)
        if n_samples:
            X = shap.utils.sample(X, n_samples)

        return X
