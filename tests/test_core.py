"""
Test suite for Viasion core components
"""

import pytest
import numpy as np
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from viasion.core.pose_estimator import PoseEstimator, PoseLandmark, PoseData
from viasion.core.object_detector import ObjectDetector, DetectedObject, DetectionResult
from viasion.core.insights_engine import InsightsEngine, PerformanceMetric, Insight


class TestPoseEstimator:
    """Test pose estimation functionality"""
    
    def test_initialization(self):
        """Test pose estimator initialization"""
        estimator = PoseEstimator(
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5
        )
        assert estimator is not None
        assert estimator.mp_pose is not None
        estimator.close()
    
    def test_process_frame_with_empty_image(self):
        """Test processing an empty image"""
        estimator = PoseEstimator()
        
        # Create blank image
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        
        # Process should return None for no pose
        result = estimator.process_frame(frame)
        assert result is None or isinstance(result, PoseData)
        
        estimator.close()
    
    def test_calculate_angle(self):
        """Test angle calculation between three points"""
        estimator = PoseEstimator()
        
        # Create three points forming a 90-degree angle
        point1 = PoseLandmark(x=0.0, y=0.0, z=0.0, visibility=1.0)
        point2 = PoseLandmark(x=1.0, y=0.0, z=0.0, visibility=1.0)
        point3 = PoseLandmark(x=1.0, y=1.0, z=0.0, visibility=1.0)
        
        angle = estimator.calculate_angle(point1, point2, point3)
        
        # Should be approximately 90 degrees
        assert 85 <= angle <= 95
        
        estimator.close()
    
    def test_landmark_names(self):
        """Test that landmark names are defined"""
        estimator = PoseEstimator()
        
        assert len(estimator.LANDMARK_NAMES) > 0
        assert "left_shoulder" in estimator.LANDMARK_NAMES
        assert "right_knee" in estimator.LANDMARK_NAMES
        
        estimator.close()


class TestObjectDetector:
    """Test object detection functionality"""
    
    def test_initialization(self):
        """Test object detector initialization"""
        detector = ObjectDetector(
            model_name="yolov8n.pt",
            confidence_threshold=0.5
        )
        assert detector is not None
        assert detector.confidence_threshold == 0.5
        detector.close()
    
    def test_process_frame_with_empty_image(self):
        """Test processing an empty image"""
        detector = ObjectDetector()
        
        # Create blank image
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        
        # Process should return DetectionResult
        result = detector.process_frame(frame, timestamp=0.0)
        assert isinstance(result, DetectionResult)
        assert result.timestamp == 0.0
        assert result.frame_size == (640, 480)
        
        detector.close()
    
    def test_get_color_for_class(self):
        """Test consistent color generation for classes"""
        detector = ObjectDetector()
        
        # Same class should get same color
        color1 = detector._get_color_for_class(5)
        color2 = detector._get_color_for_class(5)
        assert color1 == color2
        
        # Different classes should (likely) get different colors
        color3 = detector._get_color_for_class(10)
        # Not guaranteed to be different but very likely
        
        detector.close()
    
    def test_calculate_distance(self):
        """Test distance calculation between landmark and object"""
        detector = ObjectDetector()
        
        # Create test data
        landmark = PoseLandmark(x=0.5, y=0.5, z=0.0, visibility=1.0)
        detected_obj = DetectedObject(
            class_id=0,
            class_name="test",
            confidence=0.9,
            bbox=(100, 100, 200, 200),
            center=(320.0, 240.0)
        )
        frame_size = (640, 480)
        
        distance = detector.calculate_distance_to_object(
            landmark, detected_obj, frame_size
        )
        
        # Distance should be normalized (0-1)
        assert 0 <= distance <= 1
        
        detector.close()


class TestInsightsEngine:
    """Test AI insights generation"""
    
    def test_initialization(self):
        """Test insights engine initialization"""
        engine = InsightsEngine(domain="sports", history_size=50)
        
        assert engine.domain == "sports"
        assert engine.history_size == 50
        assert len(engine.pose_history) == 0
        
        engine.reset()
    
    def test_start_session(self):
        """Test starting an analysis session"""
        engine = InsightsEngine()
        
        engine.start_session("test_session_001")
        
        assert engine.session_id == "test_session_001"
        assert engine.session_start_time is not None
        assert engine.frame_count == 0
        
        engine.reset()
    
    def test_domain_thresholds(self):
        """Test domain-specific threshold loading"""
        # Sports domain
        engine_sports = InsightsEngine(domain="sports")
        thresholds_sports = engine_sports.thresholds
        
        assert "min_confidence" in thresholds_sports
        assert "max_angle_deviation" in thresholds_sports
        
        # Safety domain
        engine_safety = InsightsEngine(domain="safety")
        thresholds_safety = engine_safety.thresholds
        
        # Safety should have stricter confidence
        assert thresholds_safety["min_confidence"] >= thresholds_sports["min_confidence"]
        
        engine_sports.reset()
        engine_safety.reset()
    
    def test_analyze_frame_without_data(self):
        """Test analyzing frame with no pose/object data"""
        engine = InsightsEngine()
        engine.start_session("test")
        
        # Analyze with no data
        insights = engine.analyze_frame(None, None, None)
        
        # Should not crash and return empty or minimal insights
        assert isinstance(insights, list)
        
        engine.reset()
    
    def test_calculate_metrics(self):
        """Test metrics calculation"""
        engine = InsightsEngine()
        engine.start_session("test")
        
        # Add some dummy pose data
        for i in range(10):
            pose_data = PoseData(
                landmarks={
                    "left_shoulder": PoseLandmark(0.3, 0.3, 0.0, 0.9),
                    "right_shoulder": PoseLandmark(0.7, 0.3, 0.0, 0.9),
                },
                timestamp=i * 0.1,
                confidence=0.85
            )
            engine.pose_history.append(pose_data)
        
        metrics = engine.calculate_metrics()
        
        assert isinstance(metrics, dict)
        if "avg_confidence" in metrics:
            metric = metrics["avg_confidence"]
            assert isinstance(metric, PerformanceMetric)
            assert 0 <= metric.value <= 1
        
        engine.reset()
    
    def test_generate_recommendations(self):
        """Test recommendation generation"""
        engine = InsightsEngine()
        engine.start_session("test")
        
        # Generate some insights
        engine.insights_generated = [
            Insight("precision", "warning", "Test warning", 0.0),
            Insight("efficiency", "info", "Test info", 0.1),
        ]
        
        recommendations = engine.get_recommendations()
        
        assert isinstance(recommendations, list)
        assert len(recommendations) > 0
        assert all(isinstance(r, str) for r in recommendations)
        
        engine.reset()


class TestIntegration:
    """Integration tests combining multiple components"""
    
    def test_full_pipeline(self):
        """Test complete analysis pipeline"""
        # Initialize components
        pose_estimator = PoseEstimator()
        object_detector = ObjectDetector()
        insights_engine = InsightsEngine()
        
        # Start session
        insights_engine.start_session("integration_test")
        
        # Create test image
        frame = np.ones((480, 640, 3), dtype=np.uint8) * 128
        
        # Process frame
        pose_data = pose_estimator.process_frame(frame, 0.0)
        detection_result = object_detector.process_frame(frame, 0.0)
        
        joint_angles = {}
        if pose_data:
            joint_angles = pose_estimator.get_joint_angles(pose_data)
        
        insights = insights_engine.analyze_frame(
            pose_data, detection_result, joint_angles
        )
        
        # Verify results
        assert isinstance(insights, list)
        
        # Cleanup
        pose_estimator.close()
        object_detector.close()
        insights_engine.reset()


def test_pose_landmark_to_dict():
    """Test PoseLandmark serialization"""
    landmark = PoseLandmark(x=0.5, y=0.6, z=0.1, visibility=0.9)
    data = landmark.to_dict()
    
    assert data["x"] == 0.5
    assert data["y"] == 0.6
    assert data["z"] == 0.1
    assert data["visibility"] == 0.9


def test_detected_object_to_dict():
    """Test DetectedObject serialization"""
    obj = DetectedObject(
        class_id=1,
        class_name="person",
        confidence=0.95,
        bbox=(10, 20, 100, 200),
        center=(55.0, 110.0)
    )
    data = obj.to_dict()
    
    assert data["class_id"] == 1
    assert data["class_name"] == "person"
    assert data["confidence"] == 0.95
    assert data["bbox"] == (10, 20, 100, 200)


def test_performance_metric_to_dict():
    """Test PerformanceMetric serialization"""
    metric = PerformanceMetric(
        name="Test Metric",
        value=85.5,
        unit="score",
        optimal_range=(80, 100),
        status="good"
    )
    data = metric.to_dict()
    
    assert data["name"] == "Test Metric"
    assert data["value"] == 85.5
    assert data["unit"] == "score"
    assert data["status"] == "good"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
