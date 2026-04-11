"""Expected goals (xG) model.

Usage:
    from src.tools.xg import train_xg, evaluate_xg, predict_xg

    # Train model
    train_xg(train_seasons=["20192020", ..., "20232024"])

    # Evaluate on holdout
    evaluate_xg(test_seasons=["20242025"])

    # Predict for shots in DB
    predict_xg()  # Scores all unscored shot_attempts
"""

from src.tools.xg.model import XGModel, load_shot_data


def train_xg(train_seasons: list[str], save_path: str | None = None) -> XGModel:
    """Train xG model and save to disk."""
    model = XGModel()
    model.train(train_seasons=train_seasons)
    model.save(save_path)
    return model


def evaluate_xg(test_seasons: list[str], model_path: str | None = None) -> dict:
    """Load trained model and evaluate on holdout seasons."""
    model = XGModel.load(model_path)
    return model.evaluate(test_seasons=test_seasons)
