"""
Viasion - AI-driven training platform
Transforms human motion and object interaction into measurable insights
"""

__version__ = "0.1.0"

from .core.pose_estimator import PoseEstimator
from .core.object_detector import ObjectDetector
from .core.insights_engine import InsightsEngine
from .training.session_manager import SessionManager
from .training.domain_configs import TrainingDomain

__all__ = [
    "PoseEstimator",
    "ObjectDetector",
    "InsightsEngine",
    "SessionManager",
    "TrainingDomain",
]
