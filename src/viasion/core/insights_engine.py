"""
Insights Engine Module
AI-driven analysis of pose and object interactions for training feedback
"""

import numpy as np
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from collections import deque
import time


@dataclass
class PerformanceMetric:
    """Individual performance metric"""
    name: str
    value: float
    unit: str
    optimal_range: Optional[Tuple[float, float]] = None
    status: str = "neutral"  # good, warning, poor, neutral
    
    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "optimal_range": self.optimal_range,
            "status": self.status
        }


@dataclass
class Insight:
    """AI-driven insight or recommendation"""
    category: str  # form, efficiency, safety, precision
    severity: str  # info, warning, critical
    message: str
    timestamp: float
    metric_related: Optional[str] = None
    
    def to_dict(self) -> Dict:
        return {
            "category": self.category,
            "severity": self.severity,
            "message": self.message,
            "timestamp": self.timestamp,
            "metric_related": self.metric_related
        }


@dataclass
class SessionAnalytics:
    """Complete session analytics"""
    session_id: str
    duration: float
    metrics: Dict[str, PerformanceMetric]
    insights: List[Insight]
    score: float  # Overall performance score 0-100
    improvement_areas: List[str]
    
    def to_dict(self) -> Dict:
        return {
            "session_id": self.session_id,
            "duration": self.duration,
            "metrics": {k: v.to_dict() for k, v in self.metrics.items()},
            "insights": [i.to_dict() for i in self.insights],
            "score": self.score,
            "improvement_areas": self.improvement_areas
        }


class InsightsEngine:
    """
    AI-driven insights engine for analyzing human performance
    Provides real-time feedback and actionable recommendations
    """
    
    def __init__(self, domain: str = "general", history_size: int = 100):
        """
        Initialize insights engine
        
        Args:
            domain: Training domain (sports, safety, rehabilitation, etc.)
            history_size: Number of frames to keep in history for analysis
        """
        self.domain = domain
        self.history_size = history_size
        
        # History buffers
        self.pose_history = deque(maxlen=history_size)
        self.object_history = deque(maxlen=history_size)
        self.angle_history = deque(maxlen=history_size)
        
        # Insights tracking
        self.insights_generated = []
        self.session_start_time = None
        self.frame_count = 0
        
        # Domain-specific thresholds
        self.thresholds = self._load_domain_thresholds(domain)
    
    def _load_domain_thresholds(self, domain: str) -> Dict[str, Any]:
        """
        Load domain-specific thresholds and criteria
        
        Args:
            domain: Training domain
            
        Returns:
            Dictionary of thresholds
        """
        # Default thresholds
        base_thresholds = {
            "min_confidence": 0.6,
            "max_angle_deviation": 15.0,
            "stability_threshold": 5.0,
            "min_visibility": 0.5,
            "interaction_distance": 0.15,
        }
        
        # Domain-specific adjustments
        domain_adjustments = {
            "sports": {
                "max_angle_deviation": 10.0,
                "stability_threshold": 3.0,
            },
            "safety": {
                "min_confidence": 0.7,
                "max_angle_deviation": 20.0,
                "min_visibility": 0.6,
            },
            "rehabilitation": {
                "max_angle_deviation": 25.0,
                "stability_threshold": 8.0,
            },
            "manufacturing": {
                "min_confidence": 0.65,
                "interaction_distance": 0.12,
            }
        }
        
        thresholds = base_thresholds.copy()
        if domain in domain_adjustments:
            thresholds.update(domain_adjustments[domain])
        
        return thresholds
    
    def start_session(self, session_id: str):
        """
        Start a new analysis session
        
        Args:
            session_id: Unique session identifier
        """
        self.session_id = session_id
        self.session_start_time = time.time()
        self.frame_count = 0
        self.insights_generated = []
        self.pose_history.clear()
        self.object_history.clear()
        self.angle_history.clear()
    
    def analyze_frame(
        self,
        pose_data: Optional['PoseData'],
        detection_result: Optional['DetectionResult'],
        joint_angles: Optional[Dict[str, float]] = None
    ) -> List[Insight]:
        """
        Analyze a single frame and generate insights
        
        Args:
            pose_data: Pose detection data
            detection_result: Object detection results
            joint_angles: Pre-calculated joint angles
            
        Returns:
            List of new insights generated
        """
        self.frame_count += 1
        current_time = time.time()
        
        # Store in history
        if pose_data:
            self.pose_history.append(pose_data)
        if detection_result:
            self.object_history.append(detection_result)
        if joint_angles:
            self.angle_history.append(joint_angles)
        
        # Generate insights
        new_insights = []
        
        # Analyze pose quality
        if pose_data:
            pose_insights = self._analyze_pose_quality(pose_data, current_time)
            new_insights.extend(pose_insights)
        
        # Analyze movement stability
        if len(self.angle_history) >= 10:
            stability_insights = self._analyze_stability(current_time)
            new_insights.extend(stability_insights)
        
        # Analyze object interactions
        if pose_data and detection_result:
            interaction_insights = self._analyze_interactions(
                pose_data, detection_result, current_time
            )
            new_insights.extend(interaction_insights)
        
        # Store insights
        self.insights_generated.extend(new_insights)
        
        return new_insights
    
    def _analyze_pose_quality(
        self,
        pose_data: 'PoseData',
        timestamp: float
    ) -> List[Insight]:
        """Analyze pose detection quality"""
        insights = []
        
        # Check overall confidence
        if pose_data.confidence < self.thresholds["min_confidence"]:
            insights.append(Insight(
                category="precision",
                severity="warning",
                message=f"Low pose detection confidence ({pose_data.confidence:.2f}). Ensure good lighting and full body visibility.",
                timestamp=timestamp,
                metric_related="confidence"
            ))
        
        # Check landmark visibility
        low_visibility_landmarks = [
            name for name, lm in pose_data.landmarks.items()
            if lm.visibility < self.thresholds["min_visibility"]
        ]
        
        if len(low_visibility_landmarks) > 5:
            insights.append(Insight(
                category="precision",
                severity="warning",
                message=f"{len(low_visibility_landmarks)} body parts have low visibility. Adjust camera position.",
                timestamp=timestamp,
                metric_related="visibility"
            ))
        
        return insights
    
    def _analyze_stability(self, timestamp: float) -> List[Insight]:
        """Analyze movement stability from angle history"""
        insights = []
        
        if len(self.angle_history) < 10:
            return insights
        
        # Calculate angle variations for each joint
        recent_angles = list(self.angle_history)[-10:]
        
        for joint_name in recent_angles[0].keys():
            angles = [frame.get(joint_name, 0) for frame in recent_angles if joint_name in frame]
            
            if len(angles) >= 5:
                std_dev = np.std(angles)
                
                if std_dev > self.thresholds["stability_threshold"]:
                    insights.append(Insight(
                        category="efficiency",
                        severity="info",
                        message=f"{joint_name.replace('_', ' ').title()} shows instability (±{std_dev:.1f}°). Focus on controlled movement.",
                        timestamp=timestamp,
                        metric_related=f"stability_{joint_name}"
                    ))
        
        return insights
    
    def _analyze_interactions(
        self,
        pose_data: 'PoseData',
        detection_result: 'DetectionResult',
        timestamp: float
    ) -> List[Insight]:
        """Analyze pose-object interactions"""
        insights = []
        
        # Check if hands are near detected objects
        hand_landmarks = ["left_wrist", "right_wrist", "left_index", "right_index"]
        
        for obj in detection_result.objects:
            for hand_name in hand_landmarks:
                if hand_name in pose_data.landmarks:
                    landmark = pose_data.landmarks[hand_name]
                    
                    # Calculate distance
                    width, height = detection_result.frame_size
                    lm_x = landmark.x * width
                    lm_y = landmark.y * height
                    obj_x, obj_y = obj.center
                    
                    distance = np.sqrt((lm_x - obj_x)**2 + (lm_y - obj_y)**2)
                    max_distance = np.sqrt(width**2 + height**2)
                    normalized_distance = distance / max_distance
                    
                    if normalized_distance < self.thresholds["interaction_distance"]:
                        insights.append(Insight(
                            category="precision",
                            severity="info",
                            message=f"Interaction detected: {hand_name.replace('_', ' ')} near {obj.class_name}",
                            timestamp=timestamp,
                            metric_related="interaction"
                        ))
        
        return insights
    
    def calculate_metrics(self) -> Dict[str, PerformanceMetric]:
        """
        Calculate performance metrics from session data
        
        Returns:
            Dictionary of performance metrics
        """
        metrics = {}
        
        # Average confidence
        if self.pose_history:
            avg_confidence = np.mean([p.confidence for p in self.pose_history])
            status = "good" if avg_confidence > 0.8 else "warning" if avg_confidence > 0.6 else "poor"
            metrics["avg_confidence"] = PerformanceMetric(
                name="Average Confidence",
                value=avg_confidence,
                unit="score",
                optimal_range=(0.8, 1.0),
                status=status
            )
        
        # Movement consistency
        if len(self.angle_history) > 10:
            all_variations = []
            for joint_name in self.angle_history[0].keys():
                angles = [frame.get(joint_name, 0) for frame in self.angle_history if joint_name in frame]
                if angles:
                    all_variations.append(np.std(angles))
            
            if all_variations:
                avg_variation = np.mean(all_variations)
                status = "good" if avg_variation < 5 else "warning" if avg_variation < 10 else "poor"
                metrics["movement_consistency"] = PerformanceMetric(
                    name="Movement Consistency",
                    value=100 - min(avg_variation * 2, 100),
                    unit="score",
                    optimal_range=(80, 100),
                    status=status
                )
        
        # Frame rate
        if self.session_start_time:
            duration = time.time() - self.session_start_time
            fps = self.frame_count / duration if duration > 0 else 0
            status = "good" if fps > 20 else "warning" if fps > 10 else "poor"
            metrics["frame_rate"] = PerformanceMetric(
                name="Frame Rate",
                value=fps,
                unit="fps",
                optimal_range=(25, 60),
                status=status
            )
        
        # Interaction count
        interaction_insights = [
            i for i in self.insights_generated
            if i.metric_related == "interaction"
        ]
        metrics["interactions"] = PerformanceMetric(
            name="Object Interactions",
            value=len(interaction_insights),
            unit="count",
            status="neutral"
        )
        
        return metrics
    
    def generate_session_analytics(self) -> SessionAnalytics:
        """
        Generate comprehensive session analytics
        
        Returns:
            SessionAnalytics object
        """
        if not self.session_start_time:
            raise ValueError("No active session")
        
        duration = time.time() - self.session_start_time
        metrics = self.calculate_metrics()
        
        # Calculate overall score
        metric_scores = [
            m.value if m.unit == "score" else 50
            for m in metrics.values()
            if m.unit == "score"
        ]
        overall_score = np.mean(metric_scores) if metric_scores else 50.0
        
        # Identify improvement areas
        improvement_areas = []
        for name, metric in metrics.items():
            if metric.status == "poor":
                improvement_areas.append(metric.name)
        
        # Get critical and warning insights
        recent_insights = [
            i for i in self.insights_generated[-20:]
            if i.severity in ["warning", "critical"]
        ]
        
        return SessionAnalytics(
            session_id=self.session_id,
            duration=duration,
            metrics=metrics,
            insights=recent_insights,
            score=overall_score,
            improvement_areas=improvement_areas
        )
    
    def get_recommendations(self) -> List[str]:
        """
        Get actionable recommendations based on analysis
        
        Returns:
            List of recommendation strings
        """
        recommendations = []
        
        # Analyze recent insights for patterns
        recent_insights = self.insights_generated[-50:]
        
        # Count insights by category
        category_counts = {}
        for insight in recent_insights:
            category_counts[insight.category] = category_counts.get(insight.category, 0) + 1
        
        # Generate recommendations based on patterns
        if category_counts.get("precision", 0) > 5:
            recommendations.append(
                "Focus on precision: Multiple precision issues detected. "
                "Ensure proper camera positioning and lighting."
            )
        
        if category_counts.get("efficiency", 0) > 5:
            recommendations.append(
                "Improve movement efficiency: Work on smoother, more controlled movements."
            )
        
        if category_counts.get("safety", 0) > 0:
            recommendations.append(
                "Safety concern detected: Review proper form and technique to prevent injury."
            )
        
        # Add general recommendations
        metrics = self.calculate_metrics()
        if "avg_confidence" in metrics and metrics["avg_confidence"].status != "good":
            recommendations.append(
                "Improve detection quality: Adjust lighting and ensure full body is visible in frame."
            )
        
        if "movement_consistency" in metrics and metrics["movement_consistency"].status != "good":
            recommendations.append(
                "Practice consistency: Focus on repeating movements with similar form each time."
            )
        
        return recommendations if recommendations else ["Keep up the good work! Continue practicing to maintain performance."]
    
    def reset(self):
        """Reset the engine state"""
        self.pose_history.clear()
        self.object_history.clear()
        self.angle_history.clear()
        self.insights_generated = []
        self.session_start_time = None
        self.frame_count = 0
