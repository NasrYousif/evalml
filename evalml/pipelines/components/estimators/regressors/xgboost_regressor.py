import pandas as pd
from skopt.space import Integer, Real

from evalml.model_family import ModelFamily
from evalml.pipelines.components.estimators import Estimator
from evalml.problem_types import ProblemTypes
from evalml.utils import get_random_seed, import_or_raise


class XGBoostRegressor(Estimator):
    """XGBoost Regressor"""
    name = "XGBoost Regressor"
    hyperparameter_ranges = {
        "eta": Real(0, 1),
        "max_depth": Integer(1, 20),
        "min_child_weight": Real(1, 10),
        "n_estimators": Integer(1, 1000),
    }
    model_family = ModelFamily.XGBOOST
    supported_problem_types = [ProblemTypes.REGRESSION]

    # xgboost supports seeds from -2**31 to 2**31 - 1 inclusive. these limits ensure the random seed generated below
    # is within that range.
    SEED_MIN = -2**31
    SEED_MAX = 2**31 - 1

    def __init__(self, eta=0.1, max_depth=3, min_child_weight=1, n_estimators=100, random_state=0):
        random_seed = get_random_seed(random_state, self.SEED_MIN, self.SEED_MAX)
        parameters = {"eta": eta,
                      "max_depth": max_depth,
                      "min_child_weight": min_child_weight,
                      "n_estimators": n_estimators}
        xgb_error_msg = "XGBoost is not installed. Please install using `pip install xgboost.`"
        xgb = import_or_raise("xgboost", error_msg=xgb_error_msg)
        xgb_Regressor = xgb.XGBRegressor(random_state=random_seed,
                                         eta=eta,
                                         max_depth=max_depth,
                                         n_estimators=n_estimators,
                                         min_child_weight=min_child_weight)
        super().__init__(parameters=parameters,
                         component_obj=xgb_Regressor,
                         random_state=random_state)

    def fit(self, X, y=None):
        # necessary to convert to numpy in case input DataFrame has column names that contain symbols ([, ], <) that XGBoost cannot properly handle
        if isinstance(X, pd.DataFrame):
            X = X.to_numpy()
        return super().fit(X, y)

    def predict(self, X):
        if isinstance(X, pd.DataFrame):
            X = X.to_numpy()
        return super().predict(X)

    @property
    def feature_importances(self):
        return self._component_obj.feature_importances_