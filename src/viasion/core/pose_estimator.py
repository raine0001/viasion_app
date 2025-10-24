"""
Pose Estimator Module
Uses MediaPipe for real-time human pose detection and tracking
"""

import cv2
import mediapipe as mp
import numpy as np
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass


@dataclass
class PoseLandmark:
    """Represents a single pose landmark"""
    x: float
    y: float
    z: float
    visibility: float
    
    def to_dict(self) -> Dict:
        return {
            "x": self.x,
            "y": self.y,
            "z": self.z,
            "visibility": self.visibility
        }


@dataclass
class PoseData:
    """Complete pose detection result"""
    landmarks: Dict[str, PoseLandmark]
    timestamp: float
    confidence: float
    
    def to_dict(self) -> Dict:
        return {
            "landmarks": {k: v.to_dict() for k, v in self.landmarks.items()},
            "timestamp": self.timestamp,
            "confidence": self.confidence
        }


class PoseEstimator:
    """
    Real-time pose estimation using MediaPipe
    Detects and tracks human body landmarks for movement analysis
    """
    
    LANDMARK_NAMES = [
        "nose", "left_eye_inner", "left_eye", "left_eye_outer",
        "right_eye_inner", "right_eye", "right_eye_outer",
        "left_ear", "right_ear", "mouth_left", "mouth_right",
        "left_shoulder", "right_shoulder", "left_elbow", "right_elbow",
        "left_wrist", "right_wrist", "left_pinky", "right_pinky",
        "left_index", "right_index", "left_thumb", "right_thumb",
        "left_hip", "right_hip", "left_knee", "right_knee",
        "left_ankle", "right_ankle", "left_heel", "right_heel",
        "left_foot_index", "right_foot_index"
    ]
    
    def __init__(
        self,
        min_detection_confidence: float = 0.5,
        min_tracking_confidence: float = 0.5,
        model_complexity: int = 1
    ):
        """
        Initialize pose estimator
        
        Args:
            min_detection_confidence: Minimum confidence for detection
            min_tracking_confidence: Minimum confidence for tracking
            model_complexity: Complexity of pose model (0, 1, or 2)
        """
        self.mp_pose = mp.solutions.pose
        self.mp_drawing = mp.solutions.drawing_utils
        self.mp_drawing_styles = mp.solutions.drawing_styles
        
        self.pose = self.mp_pose.Pose(
            min_detection_confidence=min_detection_confidence,
            min_tracking_confidence=min_tracking_confidence,
            model_complexity=model_complexity
        )
        
        self.is_active = False
    
    def process_frame(self, frame: np.ndarray, timestamp: float = 0.0) -> Optional[PoseData]:
        """
        Process a single frame to detect pose
        
        Args:
            frame: Input image frame (BGR format)
            timestamp: Frame timestamp in seconds
            
        Returns:
            PoseData if pose detected, None otherwise
        """
        # Convert BGR to RGB
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        
        # Process the frame
        results = self.pose.process(rgb_frame)
        
        if not results.pose_landmarks:
            return None
        
        # Extract landmarks
        landmarks = {}
        for idx, landmark in enumerate(results.pose_landmarks.landmark):
            if idx < len(self.LANDMARK_NAMES):
                name = self.LANDMARK_NAMES[idx]
                landmarks[name] = PoseLandmark(
                    x=landmark.x,
                    y=landmark.y,
                    z=landmark.z,
                    visibility=landmark.visibility
                )
        
        # Calculate average confidence from visibility scores
        confidence = np.mean([lm.visibility for lm in landmarks.values()])
        
        return PoseData(
            landmarks=landmarks,
            timestamp=timestamp,
            confidence=confidence
        )
    
    def draw_landmarks(
        self,
        frame: np.ndarray,
        pose_data: PoseData,
        draw_connections: bool = True
    ) -> np.ndarray:
        """
        Draw pose landmarks on frame
        
        Args:
            frame: Input frame
            pose_data: Pose detection data
            draw_connections: Whether to draw skeleton connections
            
        Returns:
            Annotated frame
        """
        annotated_frame = frame.copy()
        
        # Convert landmarks back to MediaPipe format for drawing
        landmark_list = []
        for name in self.LANDMARK_NAMES:
            if name in pose_data.landmarks:
                lm = pose_data.landmarks[name]
                landmark_list.append(
                    self.mp_pose.PoseLandmark(
                        x=lm.x, y=lm.y, z=lm.z, visibility=lm.visibility
                    )
                )
        
        if landmark_list:
            # Create a landmark object
            from mediapipe.framework.formats import landmark_pb2
            pose_landmarks = landmark_pb2.NormalizedLandmarkList()
            for lm_data in landmark_list:
                landmark = pose_landmarks.landmark.add()
                landmark.x = lm_data.x
                landmark.y = lm_data.y
                landmark.z = lm_data.z
                landmark.visibility = lm_data.visibility
            
            # Draw landmarks
            self.mp_drawing.draw_landmarks(
                annotated_frame,
                pose_landmarks,
                self.mp_pose.POSE_CONNECTIONS if draw_connections else None,
                landmark_drawing_spec=self.mp_drawing_styles.get_default_pose_landmarks_style()
            )
        
        return annotated_frame
    
    def calculate_angle(
        self,
        point1: PoseLandmark,
        point2: PoseLandmark,
        point3: PoseLandmark
    ) -> float:
        """
        Calculate angle between three points
        
        Args:
            point1: First point (start of angle)
            point2: Second point (vertex of angle)
            point3: Third point (end of angle)
            
        Returns:
            Angle in degrees
        """
        # Convert to numpy arrays
        a = np.array([point1.x, point1.y])
        b = np.array([point2.x, point2.y])
        c = np.array([point3.x, point3.y])
        
        # Calculate vectors
        ba = a - b
        bc = c - b
        
        # Calculate angle
        cosine_angle = np.dot(ba, bc) / (np.linalg.norm(ba) * np.linalg.norm(bc))
        angle = np.arccos(np.clip(cosine_angle, -1.0, 1.0))
        
        return np.degrees(angle)
    
    def get_joint_angles(self, pose_data: PoseData) -> Dict[str, float]:
        """
        Calculate important joint angles
        
        Args:
            pose_data: Pose detection data
            
        Returns:
            Dictionary of joint angles
        """
        angles = {}
        landmarks = pose_data.landmarks
        
        try:
            # Left elbow angle
            if all(k in landmarks for k in ["left_shoulder", "left_elbow", "left_wrist"]):
                angles["left_elbow"] = self.calculate_angle(
                    landmarks["left_shoulder"],
                    landmarks["left_elbow"],
                    landmarks["left_wrist"]
                )
            
            # Right elbow angle
            if all(k in landmarks for k in ["right_shoulder", "right_elbow", "right_wrist"]):
                angles["right_elbow"] = self.calculate_angle(
                    landmarks["right_shoulder"],
                    landmarks["right_elbow"],
                    landmarks["right_wrist"]
                )
            
            # Left knee angle
            if all(k in landmarks for k in ["left_hip", "left_knee", "left_ankle"]):
                angles["left_knee"] = self.calculate_angle(
                    landmarks["left_hip"],
                    landmarks["left_knee"],
                    landmarks["left_ankle"]
                )
            
            # Right knee angle
            if all(k in landmarks for k in ["right_hip", "right_knee", "right_ankle"]):
                angles["right_knee"] = self.calculate_angle(
                    landmarks["right_hip"],
                    landmarks["right_knee"],
                    landmarks["right_ankle"]
                )
            
            # Left hip angle
            if all(k in landmarks for k in ["left_shoulder", "left_hip", "left_knee"]):
                angles["left_hip"] = self.calculate_angle(
                    landmarks["left_shoulder"],
                    landmarks["left_hip"],
                    landmarks["left_knee"]
                )
            
            # Right hip angle
            if all(k in landmarks for k in ["right_shoulder", "right_hip", "right_knee"]):
                angles["right_hip"] = self.calculate_angle(
                    landmarks["right_shoulder"],
                    landmarks["right_hip"],
                    landmarks["right_knee"]
                )
            
            # Left shoulder angle
            if all(k in landmarks for k in ["left_elbow", "left_shoulder", "left_hip"]):
                angles["left_shoulder"] = self.calculate_angle(
                    landmarks["left_elbow"],
                    landmarks["left_shoulder"],
                    landmarks["left_hip"]
                )
            
            # Right shoulder angle
            if all(k in landmarks for k in ["right_elbow", "right_shoulder", "right_hip"]):
                angles["right_shoulder"] = self.calculate_angle(
                    landmarks["right_elbow"],
                    landmarks["right_shoulder"],
                    landmarks["right_hip"]
                )
        except Exception as e:
            print(f"Error calculating joint angles: {e}")
        
        return angles
    
    def close(self):
        """Release resources"""
        if self.pose:
            self.pose.close()
        self.is_active = False
