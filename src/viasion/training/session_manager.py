"""
Session Manager Module
Manages training sessions and coordinates components
"""

import time
import json
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime
from dataclasses import dataclass, asdict

from ..core.pose_estimator import PoseEstimator, PoseData
from ..core.object_detector import ObjectDetector, DetectionResult
from ..core.insights_engine import InsightsEngine, SessionAnalytics
from .domain_configs import TrainingDomain, DomainConfig, DomainConfigFactory


@dataclass
class SessionConfig:
    """Configuration for a training session"""
    session_id: str
    domain: TrainingDomain
    user_id: Optional[str] = None
    exercise_name: Optional[str] = None
    save_data: bool = True
    data_directory: str = "./data/sessions"
    
    def to_dict(self) -> Dict:
        return {
            "session_id": self.session_id,
            "domain": self.domain.value,
            "user_id": self.user_id,
            "exercise_name": self.exercise_name,
            "save_data": self.save_data,
            "data_directory": self.data_directory
        }


@dataclass
class FrameAnalysis:
    """Analysis result for a single frame"""
    frame_number: int
    timestamp: float
    pose_detected: bool
    objects_detected: int
    joint_angles: Dict[str, float]
    new_insights: int
    
    def to_dict(self) -> Dict:
        return asdict(self)


class SessionManager:
    """
    Manages complete training sessions
    Coordinates pose estimation, object detection, and insights generation
    """
    
    def __init__(
        self,
        domain: TrainingDomain = TrainingDomain.GENERAL,
        pose_config: Optional[Dict] = None,
        detector_config: Optional[Dict] = None
    ):
        """
        Initialize session manager
        
        Args:
            domain: Training domain
            pose_config: Configuration for pose estimator
            detector_config: Configuration for object detector
        """
        self.domain = domain
        self.domain_config = DomainConfigFactory.create_config(domain)
        
        # Initialize components
        pose_config = pose_config or {}
        detector_config = detector_config or {}
        
        self.pose_estimator = PoseEstimator(**pose_config)
        self.object_detector = ObjectDetector(**detector_config)
        self.insights_engine = InsightsEngine(domain=domain.value)
        
        # Session state
        self.session_config: Optional[SessionConfig] = None
        self.is_active = False
        self.frame_analyses: List[FrameAnalysis] = []
        self.session_start_time: Optional[float] = None
    
    def start_session(
        self,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        exercise_name: Optional[str] = None,
        save_data: bool = True,
        data_directory: str = "./data/sessions"
    ) -> str:
        """
        Start a new training session
        
        Args:
            session_id: Unique session ID (auto-generated if not provided)
            user_id: User identifier
            exercise_name: Name of exercise being performed
            save_data: Whether to save session data
            data_directory: Directory for saving data
            
        Returns:
            Session ID
        """
        if self.is_active:
            raise RuntimeError("A session is already active. End it before starting a new one.")
        
        # Generate session ID if not provided
        if not session_id:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            session_id = f"session_{timestamp}"
        
        # Create session configuration
        self.session_config = SessionConfig(
            session_id=session_id,
            domain=self.domain,
            user_id=user_id,
            exercise_name=exercise_name,
            save_data=save_data,
            data_directory=data_directory
        )
        
        # Initialize components
        self.insights_engine.start_session(session_id)
        self.frame_analyses = []
        self.session_start_time = time.time()
        self.is_active = True
        
        print(f"Session started: {session_id}")
        print(f"Domain: {self.domain_config.name}")
        if exercise_name:
            print(f"Exercise: {exercise_name}")
        
        return session_id
    
    def process_frame(
        self,
        frame,
        frame_number: int,
        timestamp: Optional[float] = None,
        filter_object_classes: Optional[List[str]] = None
    ) -> FrameAnalysis:
        """
        Process a single frame through the complete pipeline
        
        Args:
            frame: Input frame (BGR format)
            frame_number: Frame sequence number
            timestamp: Frame timestamp (uses current time if not provided)
            filter_object_classes: Optional list of object classes to detect
            
        Returns:
            FrameAnalysis result
        """
        if not self.is_active:
            raise RuntimeError("No active session. Start a session first.")
        
        if timestamp is None:
            timestamp = time.time() - self.session_start_time
        
        # Detect pose
        pose_data = self.pose_estimator.process_frame(frame, timestamp)
        
        # Detect objects
        detection_result = self.object_detector.process_frame(
            frame, timestamp, filter_object_classes
        )
        
        # Calculate joint angles if pose detected
        joint_angles = {}
        if pose_data:
            joint_angles = self.pose_estimator.get_joint_angles(pose_data)
        
        # Generate insights
        new_insights = self.insights_engine.analyze_frame(
            pose_data, detection_result, joint_angles
        )
        
        # Create frame analysis
        analysis = FrameAnalysis(
            frame_number=frame_number,
            timestamp=timestamp,
            pose_detected=pose_data is not None,
            objects_detected=len(detection_result.objects),
            joint_angles=joint_angles,
            new_insights=len(new_insights)
        )
        
        self.frame_analyses.append(analysis)
        
        return analysis
    
    def end_session(self) -> SessionAnalytics:
        """
        End the current session and generate analytics
        
        Returns:
            Session analytics
        """
        if not self.is_active:
            raise RuntimeError("No active session to end.")
        
        # Generate session analytics
        analytics = self.insights_engine.generate_session_analytics()
        
        # Save session data if configured
        if self.session_config.save_data:
            self._save_session_data(analytics)
        
        # Clean up
        self.is_active = False
        
        print(f"Session ended: {self.session_config.session_id}")
        print(f"Duration: {analytics.duration:.2f} seconds")
        print(f"Overall score: {analytics.score:.1f}/100")
        
        return analytics
    
    def _save_session_data(self, analytics: SessionAnalytics):
        """Save session data to disk"""
        try:
            # Create data directory
            data_dir = Path(self.session_config.data_directory)
            data_dir.mkdir(parents=True, exist_ok=True)
            
            # Create session file
            session_file = data_dir / f"{self.session_config.session_id}.json"
            
            # Prepare session data
            session_data = {
                "config": self.session_config.to_dict(),
                "domain_config": {
                    "name": self.domain_config.name,
                    "description": self.domain_config.description,
                    "key_metrics": self.domain_config.key_metrics
                },
                "analytics": analytics.to_dict(),
                "frame_analyses": [fa.to_dict() for fa in self.frame_analyses],
                "recommendations": self.insights_engine.get_recommendations()
            }
            
            # Save to file
            with open(session_file, 'w') as f:
                json.dump(session_data, f, indent=2)
            
            print(f"Session data saved to: {session_file}")
        except Exception as e:
            print(f"Error saving session data: {e}")
    
    def get_current_metrics(self) -> Dict[str, Any]:
        """
        Get current session metrics
        
        Returns:
            Dictionary of current metrics
        """
        if not self.is_active:
            return {}
        
        metrics = self.insights_engine.calculate_metrics()
        return {name: metric.to_dict() for name, metric in metrics.items()}
    
    def get_recent_insights(self, count: int = 10) -> List[Dict]:
        """
        Get recent insights
        
        Args:
            count: Number of recent insights to retrieve
            
        Returns:
            List of insight dictionaries
        """
        if not self.is_active:
            return []
        
        recent = self.insights_engine.insights_generated[-count:]
        return [insight.to_dict() for insight in recent]
    
    def get_recommendations(self) -> List[str]:
        """
        Get current recommendations
        
        Returns:
            List of recommendation strings
        """
        if not self.is_active:
            return []
        
        return self.insights_engine.get_recommendations()
    
    def get_domain_info(self) -> Dict[str, Any]:
        """
        Get information about current domain
        
        Returns:
            Domain configuration dictionary
        """
        return {
            "domain": self.domain.value,
            "name": self.domain_config.name,
            "description": self.domain_config.description,
            "key_metrics": self.domain_config.key_metrics,
            "safety_rules": self.domain_config.safety_rules,
            "exercises": [
                {
                    "name": ex.name,
                    "description": ex.description,
                    "target_joints": ex.target_joints
                }
                for ex in self.domain_config.exercise_templates
            ]
        }
    
    def get_session_summary(self) -> Dict[str, Any]:
        """
        Get summary of current session
        
        Returns:
            Session summary dictionary
        """
        if not self.is_active:
            return {}
        
        duration = time.time() - self.session_start_time
        
        return {
            "session_id": self.session_config.session_id,
            "domain": self.domain.value,
            "duration": duration,
            "frames_processed": len(self.frame_analyses),
            "avg_fps": len(self.frame_analyses) / duration if duration > 0 else 0,
            "poses_detected": sum(1 for fa in self.frame_analyses if fa.pose_detected),
            "total_objects_detected": sum(fa.objects_detected for fa in self.frame_analyses),
            "insights_generated": len(self.insights_engine.insights_generated)
        }
    
    def close(self):
        """Release all resources"""
        if self.is_active:
            self.end_session()
        
        self.pose_estimator.close()
        self.object_detector.close()
        self.insights_engine.reset()
