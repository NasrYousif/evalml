from scipy.optimize import minimize_scalar

from .objective_base import ObjectiveBase


class FraudDetection(ObjectiveBase):
    needs_fitting = True
    greater_is_better = False
    uses_extra_columns = True
    needs_proba = True
    name = "Fraud Detection"
    """Finds the optimal threshold for fraud detection."""

    def __init__(self, label=1, retry_percentage=.5, interchange_fee=.02,
                 fraud_payout_percentage=1.0, amount_col='amount', verbose=True):
        """Create instance.

        Args:
            label (int) : label to optimize threshold for
            verbose (bool) : whether to print while optimizing threshold
        """
        self.label = label
        self.retry_percentage = retry_percentage
        self.interchange_fee = interchange_fee
        self.fraud_payout_percentage = fraud_payout_percentage
        self.amount_col = amount_col
        self.verbose = verbose

    def fit(self, y_prob, y, extra_cols):
        """Optimize threshold on probability estimates of the label.

        Args:
            y_prob (DataFrame) : probability estimates of labels in train set
            y (DataFrame) : true labels in train set

        Returns:
            LeadScoring : instance of self
        """
        def cost(threshold):
            return self.score_for_threshold(y, y_prob, extra_cols, threshold)

        self.optimal = minimize_scalar(cost, bounds=(0, 1), method='Bounded')

        self.threshold = self.optimal.x
        return self

    def predict(self, y_prob):
        """Predicts using the optimized threshold.

        Args:
            y_prob (DataFrame) : probability estimates for each label

        Returns:
            Series : estimated labels using optimized threshold
        """
        return y_prob > self.threshold

    def score(self, y, y_prob, extra_cols):
        """The cost function for threshold-based predictions.

        Args:
            y (DataFrame) : true labels
            y_prob (DataFrame) : probability estimates of labels
        """
        return self.score_for_threshold(y, y_prob, extra_cols, self.threshold)

    def score_for_threshold(self, y, y_prob, extra_cols, threshold):
        # import pdb; pdb.set_trace()
        y_prob = y_prob[:, 1]  # get true column

        fraud_cost = extra_cols[self.amount_col] * self.fraud_payout_percentage

        interchange_cost = extra_cols[self.amount_col] * (1 - self.retry_percentage) * self.interchange_fee

        y_hat_label = y_prob > threshold

        # payout fraud
        false_negatives = (y & ~y_hat_label) * fraud_cost

        # lost fees
        false_positives = (~y & y_hat_label) * interchange_cost

        loss = false_negatives.sum() + false_positives.sum()

        return loss