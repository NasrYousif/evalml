import pandas as pd
from sklearn.impute import SimpleImputer as SkImputer

from evalml.pipelines.components import ComponentTypes
from evalml.pipelines.components.transformers import Transformer


class SimpleImputer(Transformer):
    """Imputes missing data with either mean, median and most_frequent for numerical data or most_frequent for categorical data"""
    name = 'Simple Imputer'
    component_type = ComponentTypes.IMPUTER
    _needs_fitting = True
    hyperparameter_ranges = {"impute_strategy": ["mean", "median", "most_frequent"]}

    def __init__(self, impute_strategy="most_frequent"):
        parameters = {"impute_strategy": impute_strategy}
        imputer = SkImputer(strategy=impute_strategy)
        super().__init__(parameters=parameters,
                         component_obj=imputer,
                         random_state=0)

    def transform(self, X):
        X_t = self._component_obj.transform(X)
        if not isinstance(X_t, pd.DataFrame) and isinstance(X, pd.DataFrame):
            # skLearn's SimpleImputer loses track of column type, so we need to restore
            X_t = pd.DataFrame(X_t, columns=X.columns, index=X.index).astype(X.dtypes.to_dict())
        return X_t

    def fit_transform(self, X, y=None):
        X_t = self._component_obj.fit_transform(X, y)
        if not isinstance(X_t, pd.DataFrame) and isinstance(X, pd.DataFrame):
            # skLearn's SimpleImputer loses track of column type, so we need to restore
            X_t = pd.DataFrame(X_t, columns=X.columns, index=X.index).astype(X.dtypes.to_dict())
        return X_t