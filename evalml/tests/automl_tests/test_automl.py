import os
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest
from sklearn.model_selection import StratifiedKFold

from evalml import AutoMLSearch
from evalml.automl import TrainingValidationSplit
from evalml.data_checks import (
    DataCheck,
    DataCheckError,
    DataChecks,
    DataCheckWarning
)
from evalml.exceptions import PipelineNotFoundError
from evalml.model_family import ModelFamily
from evalml.objectives import FraudCost
from evalml.pipelines import (
    BinaryClassificationPipeline,
    MulticlassClassificationPipeline,
    RegressionPipeline
)
from evalml.pipelines.utils import get_estimators, make_pipeline
from evalml.problem_types import ProblemTypes
from evalml.tuners import NoParamsException, RandomSearchTuner


@pytest.mark.parametrize("automl_type", [ProblemTypes.REGRESSION, ProblemTypes.BINARY, ProblemTypes.MULTICLASS])
def test_search_results(X_y_regression, X_y_binary, X_y_multi, automl_type):
    expected_cv_data_keys = {'all_objective_scores', 'score', 'binary_classification_threshold'}
    automl = AutoMLSearch(problem_type=automl_type, max_pipelines=2)
    if automl_type == ProblemTypes.REGRESSION:
        expected_pipeline_class = RegressionPipeline
        X, y = X_y_regression
    elif automl_type == ProblemTypes.BINARY:
        expected_pipeline_class = BinaryClassificationPipeline
        X, y = X_y_binary
    elif automl_type == ProblemTypes.MULTICLASS:
        expected_pipeline_class = MulticlassClassificationPipeline
        X, y = X_y_multi

    automl.search(X, y)
    assert automl.results.keys() == {'pipeline_results', 'search_order'}
    assert automl.results['search_order'] == [0, 1]
    assert len(automl.results['pipeline_results']) == 2
    for pipeline_id, results in automl.results['pipeline_results'].items():
        assert results.keys() == {'id', 'pipeline_name', 'pipeline_class', 'pipeline_summary', 'parameters', 'score', 'high_variance_cv', 'training_time', 'cv_data'}
        assert results['id'] == pipeline_id
        assert isinstance(results['pipeline_name'], str)
        assert issubclass(results['pipeline_class'], expected_pipeline_class)
        assert isinstance(results['pipeline_summary'], str)
        assert isinstance(results['parameters'], dict)
        assert isinstance(results['score'], float)
        assert isinstance(results['high_variance_cv'], np.bool_)
        assert isinstance(results['cv_data'], list)
        for cv_result in results['cv_data']:
            assert cv_result.keys() == expected_cv_data_keys
            if automl_type == ProblemTypes.BINARY:
                assert isinstance(cv_result['binary_classification_threshold'], float)
            else:
                assert cv_result['binary_classification_threshold'] is None
        assert automl.get_pipeline(pipeline_id).parameters == results['parameters']
    assert isinstance(automl.rankings, pd.DataFrame)
    assert isinstance(automl.full_rankings, pd.DataFrame)
    assert np.all(automl.rankings.dtypes == pd.Series(
        [np.dtype('int64'), np.dtype('O'), np.dtype('float64'), np.dtype('bool'), np.dtype('O')],
        index=['id', 'pipeline_name', 'score', 'high_variance_cv', 'parameters']))
    assert np.all(automl.full_rankings.dtypes == pd.Series(
        [np.dtype('int64'), np.dtype('O'), np.dtype('float64'), np.dtype('bool'), np.dtype('O')],
        index=['id', 'pipeline_name', 'score', 'high_variance_cv', 'parameters']))


@patch('evalml.pipelines.BinaryClassificationPipeline.score')
@patch('evalml.pipelines.BinaryClassificationPipeline.fit')
def test_pipeline_limits(mock_fit, mock_score, caplog, X_y_binary):
    X, y = X_y_binary
    mock_score.return_value = {'Log Loss Binary': 1.0}

    automl = AutoMLSearch(problem_type='binary', max_pipelines=1)
    automl.search(X, y)
    out = caplog.text
    assert "Searching up to 1 pipelines. " in out
    assert len(automl.results['pipeline_results']) == 1

    caplog.clear()
    automl = AutoMLSearch(problem_type='binary', max_time=1)
    automl.search(X, y)
    out = caplog.text
    assert "Will stop searching for new pipelines after 1 seconds" in out
    assert len(automl.results['pipeline_results']) >= 1

    caplog.clear()
    automl = AutoMLSearch(problem_type='multiclass', max_time=1e-16)
    automl.search(X, y)
    out = caplog.text
    assert "Will stop searching for new pipelines after 0 seconds" in out
    # search will always run at least one pipeline
    assert len(automl.results['pipeline_results']) >= 1

    caplog.clear()
    automl = AutoMLSearch(problem_type='binary', max_time=1, max_pipelines=5)
    automl.search(X, y)
    out = caplog.text
    assert "Searching up to 5 pipelines. " in out
    assert "Will stop searching for new pipelines after 1 seconds" in out
    assert len(automl.results['pipeline_results']) <= 5

    caplog.clear()
    automl = AutoMLSearch(problem_type='binary')
    automl.search(X, y)
    out = caplog.text
    assert "Using default limit of max_pipelines=5." in out
    assert len(automl.results['pipeline_results']) <= 5


def test_search_order(X_y_binary):
    X, y = X_y_binary
    automl = AutoMLSearch(problem_type='binary', max_pipelines=3)
    automl.search(X, y)
    correct_order = [0, 1, 2]
    assert automl.results['search_order'] == correct_order


@patch('evalml.pipelines.BinaryClassificationPipeline.fit')
def test_pipeline_fit_raises(mock_fit, X_y_binary, caplog):
    msg = 'all your model are belong to us'
    mock_fit.side_effect = Exception(msg)
    X, y = X_y_binary
    automl = AutoMLSearch(problem_type='binary', max_pipelines=1)
    with pytest.raises(Exception, match=msg):
        automl.search(X, y, raise_errors=True)
    out = caplog.text
    assert 'Exception during automl search' in out

    caplog.clear()
    automl = AutoMLSearch(problem_type='binary', max_pipelines=1)
    automl.search(X, y, raise_errors=False)
    out = caplog.text
    assert 'Exception during automl search' in out
    pipeline_results = automl.results.get('pipeline_results', {})
    assert len(pipeline_results) == 1

    cv_scores_all = pipeline_results[0].get('cv_data', {})
    for cv_scores in cv_scores_all:
        for name, score in cv_scores['all_objective_scores'].items():
            if name in ['# Training', '# Testing']:
                assert score > 0
            else:
                assert np.isnan(score)


@patch('evalml.pipelines.BinaryClassificationPipeline.score')
def test_pipeline_score_raises(mock_score, X_y_binary, caplog):
    msg = 'all your model are belong to us'
    mock_score.side_effect = Exception(msg)
    X, y = X_y_binary
    automl = AutoMLSearch(problem_type='binary', max_pipelines=1)
    with pytest.raises(Exception, match=msg):
        automl.search(X, y, raise_errors=True)
    out = caplog.text
    assert 'Exception during automl search' in out
    pipeline_results = automl.results.get('pipeline_results', {})
    assert len(pipeline_results) == 0

    caplog.clear()
    automl = AutoMLSearch(problem_type='binary', max_pipelines=1)
    automl.search(X, y, raise_errors=False)
    out = caplog.text
    assert 'Exception during automl search' in out
    pipeline_results = automl.results.get('pipeline_results', {})
    assert len(pipeline_results) == 1
    cv_scores_all = pipeline_results[0].get('cv_data', {})
    assert np.isnan(list(cv_scores_all[0]['all_objective_scores'].values())).any()


@patch('evalml.objectives.AUC.score')
def test_objective_score_raises(mock_score, X_y_binary, caplog):
    msg = 'all your model are belong to us'
    mock_score.side_effect = Exception(msg)
    X, y = X_y_binary
    automl = AutoMLSearch(problem_type='binary', max_pipelines=1)
    automl.search(X, y, raise_errors=True)
    out = caplog.text
    assert 'Error in PipelineBase.score while scoring objective AUC: all your model are belong to us' in out
    pipeline_results = automl.results.get('pipeline_results', {})
    assert len(pipeline_results) == 1
    cv_scores_all = pipeline_results[0].get('cv_data', {})
    scores = cv_scores_all[0]['all_objective_scores']
    auc_score = scores.pop('AUC')
    assert np.isnan(auc_score)
    assert not np.isnan(list(cv_scores_all[0]['all_objective_scores'].values())).any()

    caplog.clear()
    automl = AutoMLSearch(problem_type='binary', max_pipelines=1)
    automl.search(X, y, raise_errors=False)
    out = caplog.text
    assert 'Error in PipelineBase.score while scoring objective AUC: all your model are belong to us' in out
    pipeline_results = automl.results.get('pipeline_results', {})
    assert len(pipeline_results) == 1
    cv_scores_all = pipeline_results[0].get('cv_data', {})
    scores = cv_scores_all[0]['all_objective_scores']
    auc_score = scores.pop('AUC')
    assert np.isnan(auc_score)
    assert not np.isnan(list(cv_scores_all[0]['all_objective_scores'].values())).any()


def test_rankings(X_y_binary, X_y_regression):
    X, y = X_y_binary
    model_families = ['random_forest']
    automl = AutoMLSearch(problem_type='binary', allowed_model_families=model_families, max_pipelines=3)
    automl.search(X, y)
    assert len(automl.full_rankings) == 3
    assert len(automl.rankings) == 2

    X, y = X_y_regression
    automl = AutoMLSearch(problem_type='regression', allowed_model_families=model_families, max_pipelines=3)
    automl.search(X, y)
    assert len(automl.full_rankings) == 3
    assert len(automl.rankings) == 2


@patch('evalml.objectives.BinaryClassificationObjective.optimize_threshold')
@patch('evalml.pipelines.BinaryClassificationPipeline.predict_proba')
@patch('evalml.pipelines.BinaryClassificationPipeline.score')
@patch('evalml.pipelines.BinaryClassificationPipeline.fit')
def test_automl_str_search(mock_fit, mock_score, mock_predict_proba, mock_optimize_threshold, X_y_binary):
    def _dummy_callback(param1, param2):
        return None

    X, y = X_y_binary
    search_params = {
        'problem_type': 'binary',
        'objective': 'F1',
        'max_time': 100,
        'max_pipelines': 5,
        'patience': 2,
        'tolerance': 0.5,
        'allowed_model_families': ['random_forest', 'linear_model'],
        'data_split': StratifiedKFold(5),
        'tuner_class': RandomSearchTuner,
        'start_iteration_callback': _dummy_callback,
        'add_result_callback': None,
        'additional_objectives': ['Precision', 'AUC'],
        'n_jobs': 2,
        'optimize_thresholds': True
    }

    param_str_reps = {
        'Objective': search_params['objective'],
        'Max Time': search_params['max_time'],
        'Max Pipelines': search_params['max_pipelines'],
        'Allowed Pipelines': [],
        'Patience': search_params['patience'],
        'Tolerance': search_params['tolerance'],
        'Data Splitting': 'StratifiedKFold(n_splits=5, random_state=None, shuffle=False)',
        'Tuner': 'RandomSearchTuner',
        'Start Iteration Callback': '_dummy_callback',
        'Add Result Callback': None,
        'Additional Objectives': search_params['additional_objectives'],
        'Random State': 'RandomState(MT19937)',
        'n_jobs': search_params['n_jobs'],
        'Optimize Thresholds': search_params['optimize_thresholds']
    }

    automl = AutoMLSearch(**search_params)
    mock_score.return_value = {automl.objective.name: 1.0}
    mock_optimize_threshold.return_value = 0.62
    str_rep = str(automl)
    for param, value in param_str_reps.items():
        if isinstance(value, list):
            assert f"{param}" in str_rep
            for item in value:
                assert f"\t{str(item)}" in str_rep
        else:
            assert f"{param}: {str(value)}" in str_rep
    assert "Search Results" not in str_rep

    mock_score.return_value = {automl.objective.name: 1.0}
    automl.search(X, y)
    mock_fit.assert_called()
    mock_score.assert_called()
    mock_predict_proba.assert_called()
    mock_optimize_threshold.assert_called()

    str_rep = str(automl)
    assert "Search Results:" in str_rep
    assert automl.rankings.drop(['parameters'], axis='columns').to_string() in str_rep


def test_automl_data_check_results_is_none_before_search():
    automl = AutoMLSearch(problem_type='binary', max_pipelines=1)
    assert automl.data_check_results is None


@patch('evalml.pipelines.BinaryClassificationPipeline.score')
@patch('evalml.pipelines.BinaryClassificationPipeline.fit')
def test_automl_empty_data_checks(mock_fit, mock_score):
    X = pd.DataFrame({"feature1": [1, 2, 3],
                      "feature2": [None, None, None]})
    y = pd.Series([1, 1, 1])

    mock_score.return_value = {'Log Loss Binary': 1.0}

    automl = AutoMLSearch(problem_type="binary", max_pipelines=1)

    automl.search(X, y, data_checks=[])
    assert automl.data_check_results is None
    mock_fit.assert_called()
    mock_score.assert_called()

    automl.search(X, y, data_checks="disabled")
    assert automl.data_check_results is None

    automl.search(X, y, data_checks=None)
    assert automl.data_check_results is None


@patch('evalml.data_checks.DefaultDataChecks.validate')
@patch('evalml.pipelines.BinaryClassificationPipeline.score')
@patch('evalml.pipelines.BinaryClassificationPipeline.fit')
def test_automl_default_data_checks(mock_fit, mock_score, mock_validate, X_y_binary, caplog):
    X, y = X_y_binary
    mock_score.return_value = {'Log Loss Binary': 1.0}
    mock_validate.return_value = [DataCheckWarning("default data check warning", "DefaultDataChecks")]
    automl = AutoMLSearch(problem_type='binary', max_pipelines=1)
    automl.search(X, y)
    out = caplog.text
    assert "default data check warning" in out
    assert automl.data_check_results == mock_validate.return_value
    mock_fit.assert_called()
    mock_score.assert_called()
    mock_validate.assert_called()


class MockDataCheckErrorAndWarning(DataCheck):
    def validate(self, X, y):
        return [DataCheckError("error one", self.name), DataCheckWarning("warning one", self.name)]


@pytest.mark.parametrize("data_checks",
                         [[MockDataCheckErrorAndWarning()],
                          DataChecks([MockDataCheckErrorAndWarning()])])
@patch('evalml.pipelines.BinaryClassificationPipeline.score')
@patch('evalml.pipelines.BinaryClassificationPipeline.fit')
def test_automl_data_checks_raises_error(mock_fit, mock_score, data_checks, caplog):
    X = pd.DataFrame()
    y = pd.Series()

    automl = AutoMLSearch(problem_type="binary", max_pipelines=1)

    with pytest.raises(ValueError, match="Data checks raised"):
        automl.search(X, y, data_checks=data_checks)

    out = caplog.text
    assert "error one" in out
    assert "warning one" in out
    assert automl.data_check_results == MockDataCheckErrorAndWarning().validate(X, y)


def test_automl_bad_data_check_parameter_type():
    X = pd.DataFrame()
    y = pd.Series()

    automl = AutoMLSearch(problem_type="binary", max_pipelines=1)

    with pytest.raises(ValueError, match="Parameter data_checks must be a list. Received int."):
        automl.search(X, y, data_checks=1)
    with pytest.raises(ValueError, match="All elements of parameter data_checks must be an instance of DataCheck."):
        automl.search(X, y, data_checks=[1])
    with pytest.raises(ValueError, match="If data_checks is a string, it must be either 'auto' or 'disabled'. "
                                         "Received 'default'."):
        automl.search(X, y, data_checks="default")
    with pytest.raises(ValueError, match="All elements of parameter data_checks must be an instance of DataCheck."):
        automl.search(X, y, data_checks=[DataChecks([]), 1])


def test_automl_str_no_param_search():
    automl = AutoMLSearch(problem_type='binary')

    param_str_reps = {
        'Objective': 'Log Loss Binary',
        'Max Time': 'None',
        'Max Pipelines': 'None',
        'Allowed Pipelines': [],
        'Patience': 'None',
        'Tolerance': '0.0',
        'Data Splitting': 'StratifiedKFold(n_splits=3, random_state=0, shuffle=True)',
        'Tuner': 'SKOptTuner',
        'Additional Objectives': [
            'Accuracy Binary',
            'Balanced Accuracy Binary',
            'F1',
            'Precision',
            'AUC',
            'MCC Binary'],
        'Start Iteration Callback': 'None',
        'Add Result Callback': 'None',
        'Random State': 'RandomState(MT19937)',
        'n_jobs': '-1',
        'Verbose': 'True',
        'Optimize Thresholds': 'False'
    }

    str_rep = str(automl)

    for param, value in param_str_reps.items():
        assert f"{param}" in str_rep
        if isinstance(value, list):
            value = "\n".join(["\t{}".format(item) for item in value])
            assert value in str_rep
    assert "Search Results" not in str_rep


@patch('evalml.pipelines.BinaryClassificationPipeline.score')
@patch('evalml.pipelines.BinaryClassificationPipeline.fit')
def test_automl_feature_selection(mock_fit, mock_score, X_y_binary):
    X, y = X_y_binary
    mock_score.return_value = {'Log Loss Binary': 1.0}

    class MockFeatureSelectionPipeline(BinaryClassificationPipeline):
        component_graph = ['RF Classifier Select From Model', 'Logistic Regression Classifier']

        def fit(self, X, y):
            """Mock fit, noop"""

    allowed_pipelines = [MockFeatureSelectionPipeline]
    start_iteration_callback = MagicMock()
    automl = AutoMLSearch(problem_type='binary', max_pipelines=2, start_iteration_callback=start_iteration_callback, allowed_pipelines=allowed_pipelines)
    automl.search(X, y)

    assert start_iteration_callback.call_count == 2
    proposed_parameters = start_iteration_callback.call_args_list[1][0][1]
    assert proposed_parameters.keys() == {'RF Classifier Select From Model', 'Logistic Regression Classifier'}
    assert proposed_parameters['RF Classifier Select From Model']['number_features'] == X.shape[1]


@patch('evalml.tuners.random_search_tuner.RandomSearchTuner.is_search_space_exhausted')
@patch('evalml.pipelines.BinaryClassificationPipeline.score')
@patch('evalml.pipelines.BinaryClassificationPipeline.fit')
def test_automl_tuner_exception(mock_fit, mock_score, mock_is_search_space_exhausted, X_y_binary):
    mock_score.return_value = {'Log Loss Binary': 1.0}
    X, y = X_y_binary
    error_text = "Cannot create a unique set of unexplored parameters. Try expanding the search space."
    mock_is_search_space_exhausted.side_effect = NoParamsException(error_text)
    clf = AutoMLSearch(problem_type='regression', objective="R2", tuner_class=RandomSearchTuner, max_pipelines=10)
    with pytest.raises(NoParamsException, match=error_text):
        clf.search(X, y)


@patch('evalml.automl.automl_algorithm.IterativeAlgorithm.next_batch')
@patch('evalml.pipelines.BinaryClassificationPipeline.score')
@patch('evalml.pipelines.BinaryClassificationPipeline.fit')
def test_automl_algorithm(mock_fit, mock_score, mock_algo_next_batch, X_y_binary):
    X, y = X_y_binary
    mock_score.return_value = {'Log Loss Binary': 1.0}
    mock_algo_next_batch.side_effect = StopIteration("that's all, folks")
    automl = AutoMLSearch(problem_type='binary', max_pipelines=5)
    automl.search(X, y)
    assert automl.data_check_results is None
    mock_fit.assert_called()
    mock_score.assert_called()
    assert mock_algo_next_batch.call_count == 1
    pipeline_results = automl.results.get('pipeline_results', {})
    assert len(pipeline_results) == 1
    assert pipeline_results[0].get('score') == 1.0


@patch('evalml.automl.automl_algorithm.IterativeAlgorithm.__init__')
def test_automl_allowed_pipelines_algorithm(mock_algo_init, dummy_binary_pipeline_class, X_y_binary):
    mock_algo_init.side_effect = Exception('mock algo init')
    X, y = X_y_binary

    allowed_pipelines = [dummy_binary_pipeline_class]
    automl = AutoMLSearch(problem_type='binary', allowed_pipelines=allowed_pipelines, max_pipelines=10)
    with pytest.raises(Exception, match='mock algo init'):
        automl.search(X, y)
    assert mock_algo_init.call_count == 1
    _, kwargs = mock_algo_init.call_args
    assert kwargs['max_pipelines'] == 10
    assert kwargs['allowed_pipelines'] == allowed_pipelines

    allowed_model_families = [ModelFamily.RANDOM_FOREST]
    automl = AutoMLSearch(problem_type='binary', allowed_model_families=allowed_model_families, max_pipelines=1)
    with pytest.raises(Exception, match='mock algo init'):
        automl.search(X, y)
    assert mock_algo_init.call_count == 2
    _, kwargs = mock_algo_init.call_args
    assert kwargs['max_pipelines'] == 1
    for actual, expected in zip(kwargs['allowed_pipelines'], [make_pipeline(X, y, estimator, ProblemTypes.BINARY) for estimator in get_estimators(ProblemTypes.BINARY, model_families=allowed_model_families)]):
        assert actual.parameters == expected.parameters


def test_automl_serialization(X_y_binary, tmpdir):
    X, y = X_y_binary
    path = os.path.join(str(tmpdir), 'automl.pkl')
    num_max_pipelines = 5
    automl = AutoMLSearch(problem_type='binary', max_pipelines=num_max_pipelines)
    automl.search(X, y)
    automl.save(path)
    loaded_automl = automl.load(path)
    for i in range(num_max_pipelines):
        assert automl.get_pipeline(i).__class__ == loaded_automl.get_pipeline(i).__class__
        assert automl.get_pipeline(i).parameters == loaded_automl.get_pipeline(i).parameters
        assert automl.results == loaded_automl.results
        pd.testing.assert_frame_equal(automl.rankings, loaded_automl.rankings)


def test_invalid_data_splitter():
    data_splitter = pd.DataFrame()
    with pytest.raises(ValueError, match='Not a valid data splitter'):
        AutoMLSearch(problem_type='binary', data_split=data_splitter)


@patch('evalml.pipelines.BinaryClassificationPipeline.score')
def test_large_dataset_binary(mock_score):
    X = pd.DataFrame({'col_0': [i for i in range(101000)]})
    y = pd.Series([i % 2 for i in range(101000)])

    fraud_objective = FraudCost(amount_col='col_0')

    automl = AutoMLSearch(problem_type='binary',
                          objective=fraud_objective,
                          additional_objectives=['auc', 'f1', 'precision'],
                          max_time=1,
                          max_pipelines=1,
                          optimize_thresholds=True)
    mock_score.return_value = {automl.objective.name: 1.234}
    assert automl.data_split is None
    automl.search(X, y)
    assert isinstance(automl.data_split, TrainingValidationSplit)
    assert automl.data_split.get_n_splits() == 1

    for pipeline_id in automl.results['search_order']:
        assert len(automl.results['pipeline_results'][pipeline_id]['cv_data']) == 1
        assert automl.results['pipeline_results'][pipeline_id]['cv_data'][0]['score'] == 1.234


@patch('evalml.pipelines.MulticlassClassificationPipeline.score')
def test_large_dataset_multiclass(mock_score):
    X = pd.DataFrame({'col_0': [i for i in range(101000)]})
    y = pd.Series([i % 4 for i in range(101000)])

    automl = AutoMLSearch(problem_type='multiclass', max_time=1, max_pipelines=1)
    mock_score.return_value = {automl.objective.name: 1.234}
    assert automl.data_split is None
    automl.search(X, y)
    assert isinstance(automl.data_split, TrainingValidationSplit)
    assert automl.data_split.get_n_splits() == 1

    for pipeline_id in automl.results['search_order']:
        assert len(automl.results['pipeline_results'][pipeline_id]['cv_data']) == 1
        assert automl.results['pipeline_results'][pipeline_id]['cv_data'][0]['score'] == 1.234


@patch('evalml.pipelines.RegressionPipeline.score')
def test_large_dataset_regression(mock_score):
    X = pd.DataFrame({'col_0': [i for i in range(101000)]})
    y = pd.Series([i for i in range(101000)])

    automl = AutoMLSearch(problem_type='regression', max_time=1, max_pipelines=1)
    mock_score.return_value = {automl.objective.name: 1.234}
    assert automl.data_split is None
    automl.search(X, y)
    assert isinstance(automl.data_split, TrainingValidationSplit)
    assert automl.data_split.get_n_splits() == 1

    for pipeline_id in automl.results['search_order']:
        assert len(automl.results['pipeline_results'][pipeline_id]['cv_data']) == 1
        assert automl.results['pipeline_results'][pipeline_id]['cv_data'][0]['score'] == 1.234


def test_allowed_pipelines_with_incorrect_problem_type(dummy_binary_pipeline_class):
    # checks that not setting allowed_pipelines does not error out
    AutoMLSearch(problem_type='binary')

    with pytest.raises(ValueError, match="is not compatible with problem_type"):
        AutoMLSearch(problem_type='regression', allowed_pipelines=[dummy_binary_pipeline_class])


def test_main_objective_problem_type_mismatch():
    with pytest.raises(ValueError, match="is not compatible with a"):
        AutoMLSearch(problem_type='binary', objective='R2')


def test_init_problem_type_error():
    with pytest.raises(ValueError, match=r"choose one of \(binary, multiclass, regression\) as problem_type"):
        AutoMLSearch()

    with pytest.raises(KeyError, match=r"does not exist"):
        AutoMLSearch(problem_type='multi')


def test_init_objective():
    defaults = {'multiclass': 'Log Loss Multiclass', 'binary': 'Log Loss Binary', 'regression': 'R2'}
    for problem_type in defaults:
        error_automl = AutoMLSearch(problem_type=problem_type)
        assert error_automl.objective.name == defaults[problem_type]


@patch('evalml.automl.automl_search.AutoMLSearch.search')
def test_checks_at_search_time(mock_search, dummy_regression_pipeline_class, X_y_multi):
    X, y = X_y_multi

    error_text = "in search, problem_type mismatches label type."
    mock_search.side_effect = ValueError(error_text)

    error_automl = AutoMLSearch(problem_type='regression', objective="R2")
    with pytest.raises(ValueError, match=error_text):
        error_automl.search(X, y)


def test_incompatible_additional_objectives():
    with pytest.raises(ValueError, match="is not compatible with a "):
        AutoMLSearch(problem_type='multiclass', additional_objectives=['Precision', 'AUC'])


def test_default_objective():
    correct_matches = {ProblemTypes.MULTICLASS: 'Log Loss Multiclass',
                       ProblemTypes.BINARY: 'Log Loss Binary',
                       ProblemTypes.REGRESSION: 'R2'}
    for problem_type in correct_matches:
        automl = AutoMLSearch(problem_type=problem_type)
        assert automl.objective.name == correct_matches[problem_type]

        automl = AutoMLSearch(problem_type=problem_type.name)
        assert automl.objective.name == correct_matches[problem_type]


@patch('evalml.pipelines.BinaryClassificationPipeline.score')
@patch('evalml.pipelines.BinaryClassificationPipeline.fit')
def test_add_to_rankings(mock_fit, mock_score, dummy_binary_pipeline_class, X_y_binary):
    X, y = X_y_binary
    mock_score.return_value = {'Log Loss Binary': 1.0}

    automl = AutoMLSearch(problem_type='binary', max_pipelines=1, allowed_pipelines=[dummy_binary_pipeline_class])
    automl.search(X, y)

    mock_score.return_value = {'Log Loss Binary': 0.1234}

    test_pipeline = dummy_binary_pipeline_class(parameters={})
    automl.add_to_rankings(test_pipeline, X, y)

    assert len(automl.rankings) == 2
    assert 0.1234 in automl.rankings['score'].values


@patch('evalml.pipelines.BinaryClassificationPipeline.score')
@patch('evalml.pipelines.BinaryClassificationPipeline.fit')
def test_add_to_rankings_no_search(mock_fit, mock_score, dummy_binary_pipeline_class, X_y_binary):
    X, y = X_y_binary
    automl = AutoMLSearch(problem_type='binary', max_pipelines=1, allowed_pipelines=[dummy_binary_pipeline_class])

    mock_score.return_value = {'Log Loss Binary': 0.1234}
    test_pipeline = dummy_binary_pipeline_class(parameters={})
    with pytest.raises(RuntimeError, match="Please run automl"):
        automl.add_to_rankings(test_pipeline, X, y)


@patch('evalml.pipelines.BinaryClassificationPipeline.score')
@patch('evalml.pipelines.BinaryClassificationPipeline.fit')
def test_add_to_rankings_duplicate(mock_fit, mock_score, dummy_binary_pipeline_class, X_y_binary):
    X, y = X_y_binary
    mock_score.return_value = {'Log Loss Binary': 0.1234}

    automl = AutoMLSearch(problem_type='binary', max_pipelines=1, allowed_pipelines=[dummy_binary_pipeline_class])
    automl.search(X, y)

    test_pipeline = dummy_binary_pipeline_class(parameters={})
    automl.add_to_rankings(test_pipeline, X, y)

    test_pipeline_duplicate = dummy_binary_pipeline_class(parameters={})
    assert automl.add_to_rankings(test_pipeline_duplicate, X, y) is None


@patch('evalml.pipelines.BinaryClassificationPipeline.score')
@patch('evalml.pipelines.BinaryClassificationPipeline.fit')
def test_add_to_rankings_trained(mock_fit, mock_score, dummy_binary_pipeline_class, X_y_binary):
    X, y = X_y_binary
    mock_score.return_value = {'Log Loss Binary': 1.0}

    automl = AutoMLSearch(problem_type='binary', max_pipelines=1, allowed_pipelines=[dummy_binary_pipeline_class])
    automl.search(X, y)

    mock_score.return_value = {'Log Loss Binary': 0.1234}
    test_pipeline = dummy_binary_pipeline_class(parameters={})
    automl.add_to_rankings(test_pipeline, X, y)

    class CoolBinaryClassificationPipeline(dummy_binary_pipeline_class):
        name = "Cool Binary Classification Pipeline"

    mock_fit.return_value = CoolBinaryClassificationPipeline(parameters={})
    test_pipeline_trained = CoolBinaryClassificationPipeline(parameters={}).fit(X, y)
    automl.add_to_rankings(test_pipeline_trained, X, y)

    assert list(automl.rankings['score'].values).count(0.1234) == 2


@patch('evalml.pipelines.BinaryClassificationPipeline.score')
@patch('evalml.pipelines.BinaryClassificationPipeline.fit')
def test_has_searched(mock_fit, mock_score, dummy_binary_pipeline_class, X_y_binary):
    X, y = X_y_binary

    automl = AutoMLSearch(problem_type='binary', max_pipelines=1)
    mock_score.return_value = {automl.objective.name: 1.0}
    assert not automl.has_searched

    automl.search(X, y)
    assert automl.has_searched


def test_no_search():
    automl = AutoMLSearch(problem_type='binary')
    assert isinstance(automl.rankings, pd.DataFrame)
    assert isinstance(automl.full_rankings, pd.DataFrame)

    df_columns = ["id", "pipeline_name", "score", "high_variance_cv", "parameters"]
    assert (automl.rankings.columns == df_columns).all()
    assert (automl.full_rankings.columns == df_columns).all()

    assert automl._data_check_results is None

    with pytest.raises(PipelineNotFoundError):
        automl.best_pipeline

    with pytest.raises(PipelineNotFoundError):
        automl.get_pipeline(0)

    with pytest.raises(PipelineNotFoundError):
        automl.describe_pipeline(0)


@patch('evalml.pipelines.BinaryClassificationPipeline.score')
@patch('evalml.pipelines.BinaryClassificationPipeline.fit')
def test_get_pipeline_invalid(mock_fit, mock_score, X_y_binary):
    X, y = X_y_binary
    mock_score.return_value = {'Log Loss Binary': 1.0}

    automl = AutoMLSearch(problem_type='binary')
    with pytest.raises(PipelineNotFoundError, match="Pipeline not found in automl results"):
        automl.get_pipeline(1000)

    automl = AutoMLSearch(problem_type='binary', max_pipelines=1)
    automl.search(X, y)
    assert automl.get_pipeline(0).name == 'Mode Baseline Binary Classification Pipeline'
    automl._results['pipeline_results'][0].pop('pipeline_class')
    with pytest.raises(PipelineNotFoundError, match="Pipeline class or parameters not found in automl results"):
        automl.get_pipeline(0)

    automl = AutoMLSearch(problem_type='binary', max_pipelines=1)
    automl.search(X, y)
    assert automl.get_pipeline(0).name == 'Mode Baseline Binary Classification Pipeline'
    automl._results['pipeline_results'][0].pop('parameters')
    with pytest.raises(PipelineNotFoundError, match="Pipeline class or parameters not found in automl results"):
        automl.get_pipeline(0)


@patch('evalml.pipelines.BinaryClassificationPipeline.score')
@patch('evalml.pipelines.BinaryClassificationPipeline.fit')
def test_describe_pipeline(mock_fit, mock_score, caplog, X_y_binary):
    X, y = X_y_binary
    mock_score.return_value = {'Log Loss Binary': 1.0}

    automl = AutoMLSearch(problem_type='binary', max_pipelines=1)
    automl.search(X, y)
    out = caplog.text

    assert "Searching up to 1 pipelines. " in out

    assert len(automl.results['pipeline_results']) == 1
    caplog.clear()
    automl.describe_pipeline(0)
    out = caplog.text
    assert "Mode Baseline Binary Classification Pipeline" in out
    assert "Problem Type: Binary Classification" in out
    assert "Model Family: Baseline" in out
    assert "* strategy : random_weighted" in out
    assert "Total training time (including CV): " in out
    assert "Log Loss Binary # Training # Testing" in out
    assert "0                      1.000     66.000    34.000" in out
    assert "1                      1.000     67.000    33.000" in out
    assert "2                      1.000     67.000    33.000" in out
    assert "mean                   1.000          -         -" in out
    assert "std                    0.000          -         -" in out
    assert "coef of var            0.000          -         -" in out


@patch('evalml.pipelines.BinaryClassificationPipeline.score')
@patch('evalml.pipelines.BinaryClassificationPipeline.fit')
def test_results_getter(mock_fit, mock_score, caplog, X_y_binary):
    X, y = X_y_binary
    automl = AutoMLSearch(problem_type='binary', max_pipelines=1)

    assert automl.results == {'pipeline_results': {}, 'search_order': []}

    mock_score.return_value = {'Log Loss Binary': 1.0}
    automl.search(X, y)

    assert automl.results['pipeline_results'][0]['score'] == 1.0

    with pytest.raises(AttributeError, match='set attribute'):
        automl.results = 2.0

    automl.results['pipeline_results'][0]['score'] = 2.0
    assert automl.results['pipeline_results'][0]['score'] == 1.0