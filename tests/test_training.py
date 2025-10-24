"""
Test suite for Viasion training components
"""

import pytest
import numpy as np
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from viasion.training.domain_configs import (
    TrainingDomain, DomainConfig, DomainConfigFactory, ExerciseTemplate
)
from viasion.training.session_manager import SessionManager, SessionConfig
from viasion.core.pose_estimator import PoseData, PoseLandmark


class TestDomainConfigs:
    """Test training domain configurations"""
    
    def test_training_domain_enum(self):
        """Test TrainingDomain enum values"""
        assert TrainingDomain.SPORTS.value == "sports"
        assert TrainingDomain.SAFETY.value == "safety"
        assert TrainingDomain.REHABILITATION.value == "rehabilitation"
        assert TrainingDomain.GENERAL.value == "general"
    
    def test_domain_config_factory(self):
        """Test domain configuration creation"""
        config = DomainConfigFactory.create_config(TrainingDomain.SPORTS)
        
        assert isinstance(config, DomainConfig)
        assert config.domain == TrainingDomain.SPORTS
        assert config.name == "Sports Training"
        assert len(config.key_metrics) > 0
        assert len(config.safety_rules) > 0
    
    def test_all_domains_have_configs(self):
        """Test that all domains have configurations"""
        for domain in TrainingDomain:
            config = DomainConfigFactory.create_config(domain)
            assert isinstance(config, DomainConfig)
            assert config.domain == domain
    
    def test_sports_config_details(self):
        """Test sports domain configuration details"""
        config = DomainConfigFactory.create_config(TrainingDomain.SPORTS)
        
        assert "Sports Training" in config.name
        assert len(config.exercise_templates) > 0
        assert "movement_speed" in config.key_metrics or "form_accuracy" in config.key_metrics
        assert "min_confidence" in config.performance_thresholds
    
    def test_safety_config_details(self):
        """Test safety domain configuration details"""
        config = DomainConfigFactory.create_config(TrainingDomain.SAFETY)
        
        assert "Safety" in config.name
        assert len(config.safety_rules) > 0
        
        # Safety should have stricter thresholds
        sports_config = DomainConfigFactory.create_config(TrainingDomain.SPORTS)
        assert config.performance_thresholds["min_confidence"] >= \
               sports_config.performance_thresholds["min_confidence"]
    
    def test_exercise_template(self):
        """Test exercise template structure"""
        template = ExerciseTemplate(
            name="Test Exercise",
            description="Test description",
            target_joints=["left_knee", "right_knee"],
            optimal_angles={"left_knee": (70, 110)},
            required_objects=["ball"],
            repetitions=10
        )
        
        assert template.name == "Test Exercise"
        assert len(template.target_joints) == 2
        assert template.repetitions == 10
        
        data = template.to_dict()
        assert data["name"] == "Test Exercise"
    
    def test_domain_config_serialization(self):
        """Test domain config to_dict method"""
        config = DomainConfigFactory.create_config(TrainingDomain.FITNESS)
        data = config.to_dict()
        
        assert data["domain"] == "fitness"
        assert "name" in data
        assert "description" in data
        assert "key_metrics" in data


class TestSessionManager:
    """Test session management functionality"""
    
    def test_initialization(self):
        """Test session manager initialization"""
        manager = SessionManager(domain=TrainingDomain.SPORTS)
        
        assert manager.domain == TrainingDomain.SPORTS
        assert manager.pose_estimator is not None
        assert manager.object_detector is not None
        assert manager.insights_engine is not None
        assert not manager.is_active
        
        manager.close()
    
    def test_start_session(self):
        """Test starting a training session"""
        manager = SessionManager(domain=TrainingDomain.FITNESS)
        
        session_id = manager.start_session(
            user_id="test_user",
            exercise_name="Push-ups"
        )
        
        assert session_id is not None
        assert manager.is_active
        assert manager.session_config.user_id == "test_user"
        assert manager.session_config.exercise_name == "Push-ups"
        
        manager.close()
    
    def test_start_session_auto_id(self):
        """Test session with auto-generated ID"""
        manager = SessionManager()
        
        session_id = manager.start_session()
        
        assert session_id is not None
        assert "session_" in session_id
        assert manager.is_active
        
        manager.close()
    
    def test_cannot_start_multiple_sessions(self):
        """Test that multiple simultaneous sessions are prevented"""
        manager = SessionManager()
        
        manager.start_session()
        
        with pytest.raises(RuntimeError):
            manager.start_session()
        
        manager.close()
    
    def test_process_frame(self):
        """Test frame processing"""
        manager = SessionManager()
        manager.start_session()
        
        # Create test frame
        frame = np.ones((480, 640, 3), dtype=np.uint8) * 128
        
        analysis = manager.process_frame(frame, frame_number=0)
        
        assert analysis is not None
        assert analysis.frame_number == 0
        assert isinstance(analysis.pose_detected, bool)
        assert isinstance(analysis.objects_detected, int)
        
        manager.close()
    
    def test_process_frame_without_session(self):
        """Test that processing without session raises error"""
        manager = SessionManager()
        
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        
        with pytest.raises(RuntimeError):
            manager.process_frame(frame, 0)
        
        manager.close()
    
    def test_end_session(self):
        """Test ending a session"""
        manager = SessionManager()
        manager.start_session()
        
        # Process some frames
        frame = np.ones((480, 640, 3), dtype=np.uint8) * 128
        for i in range(5):
            manager.process_frame(frame, i)
        
        analytics = manager.end_session()
        
        assert analytics is not None
        assert not manager.is_active
        assert analytics.duration > 0
        assert len(analytics.metrics) > 0
        
        manager.close()
    
    def test_end_session_without_active(self):
        """Test ending session when none is active"""
        manager = SessionManager()
        
        with pytest.raises(RuntimeError):
            manager.end_session()
        
        manager.close()
    
    def test_get_current_metrics(self):
        """Test getting current metrics"""
        manager = SessionManager()
        manager.start_session()
        
        # Process some frames
        frame = np.ones((480, 640, 3), dtype=np.uint8) * 128
        for i in range(3):
            manager.process_frame(frame, i)
        
        metrics = manager.get_current_metrics()
        
        assert isinstance(metrics, dict)
        # May or may not have metrics depending on processing
        
        manager.close()
    
    def test_get_recent_insights(self):
        """Test getting recent insights"""
        manager = SessionManager()
        manager.start_session()
        
        insights = manager.get_recent_insights(count=5)
        
        assert isinstance(insights, list)
        
        manager.close()
    
    def test_get_recommendations(self):
        """Test getting recommendations"""
        manager = SessionManager()
        manager.start_session()
        
        recommendations = manager.get_recommendations()
        
        assert isinstance(recommendations, list)
        # Should always have at least one recommendation
        assert len(recommendations) > 0
        
        manager.close()
    
    def test_get_domain_info(self):
        """Test getting domain information"""
        manager = SessionManager(domain=TrainingDomain.SPORTS)
        
        info = manager.get_domain_info()
        
        assert info["domain"] == "sports"
        assert "name" in info
        assert "description" in info
        assert "key_metrics" in info
        assert "safety_rules" in info
        
        manager.close()
    
    def test_get_session_summary(self):
        """Test getting session summary"""
        manager = SessionManager()
        manager.start_session(session_id="test_123")
        
        # Process frames
        frame = np.ones((480, 640, 3), dtype=np.uint8) * 128
        for i in range(5):
            manager.process_frame(frame, i)
        
        summary = manager.get_session_summary()
        
        assert summary["session_id"] == "test_123"
        assert summary["frames_processed"] == 5
        assert summary["duration"] > 0
        
        manager.close()
    
    def test_session_config_serialization(self):
        """Test SessionConfig serialization"""
        config = SessionConfig(
            session_id="test_001",
            domain=TrainingDomain.SPORTS,
            user_id="user123",
            exercise_name="Squats"
        )
        
        data = config.to_dict()
        
        assert data["session_id"] == "test_001"
        assert data["domain"] == "sports"
        assert data["user_id"] == "user123"


class TestIntegration:
    """Integration tests for training components"""
    
    def test_multi_domain_sessions(self):
        """Test creating sessions with different domains"""
        domains = [
            TrainingDomain.SPORTS,
            TrainingDomain.FITNESS,
            TrainingDomain.SAFETY
        ]
        
        for domain in domains:
            manager = SessionManager(domain=domain)
            manager.start_session()
            
            assert manager.domain == domain
            assert manager.domain_config.domain == domain
            
            manager.close()
    
    def test_complete_workflow(self):
        """Test complete training workflow"""
        # Initialize manager
        manager = SessionManager(domain=TrainingDomain.FITNESS)
        
        # Get domain info
        info = manager.get_domain_info()
        assert info is not None
        
        # Start session
        session_id = manager.start_session(
            user_id="test_user",
            exercise_name="Push-ups"
        )
        
        # Process multiple frames
        frame = np.ones((480, 640, 3), dtype=np.uint8) * 128
        for i in range(10):
            analysis = manager.process_frame(frame, i, timestamp=i * 0.1)
            assert analysis.frame_number == i
        
        # Check metrics
        metrics = manager.get_current_metrics()
        assert isinstance(metrics, dict)
        
        # Get insights
        insights = manager.get_recent_insights(5)
        assert isinstance(insights, list)
        
        # Get recommendations
        recs = manager.get_recommendations()
        assert len(recs) > 0
        
        # End session
        analytics = manager.end_session()
        assert analytics.session_id == session_id
        assert analytics.duration > 0
        
        # Cleanup
        manager.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
