"""
Object Detector Module
Uses YOLO for real-time object detection and tracking
"""

import cv2
import numpy as np
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from ultralytics import YOLO


@dataclass
class DetectedObject:
    """Represents a detected object"""
    class_id: int
    class_name: str
    confidence: float
    bbox: Tuple[int, int, int, int]  # x1, y1, x2, y2
    center: Tuple[float, float]
    
    def to_dict(self) -> Dict:
        return {
            "class_id": self.class_id,
            "class_name": self.class_name,
            "confidence": self.confidence,
            "bbox": self.bbox,
            "center": self.center
        }


@dataclass
class DetectionResult:
    """Complete detection result for a frame"""
    objects: List[DetectedObject]
    timestamp: float
    frame_size: Tuple[int, int]  # width, height
    
    def to_dict(self) -> Dict:
        return {
            "objects": [obj.to_dict() for obj in self.objects],
            "timestamp": self.timestamp,
            "frame_size": self.frame_size
        }


class ObjectDetector:
    """
    Real-time object detection using YOLO
    Detects and tracks objects for interaction analysis
    """
    
    def __init__(
        self,
        model_name: str = "yolov8n.pt",
        confidence_threshold: float = 0.5,
        device: str = "cpu"
    ):
        """
        Initialize object detector
        
        Args:
            model_name: YOLO model to use (yolov8n, yolov8s, yolov8m, etc.)
            confidence_threshold: Minimum confidence for detections
            device: Device to run inference on ('cpu' or 'cuda')
        """
        self.model_name = model_name
        self.confidence_threshold = confidence_threshold
        self.device = device
        
        # Load YOLO model
        try:
            self.model = YOLO(model_name)
            self.model.to(device)
        except Exception as e:
            print(f"Error loading YOLO model: {e}")
            print("Model will be downloaded on first use")
            self.model = None
        
        self.class_names = None
        self.is_active = False
    
    def _ensure_model_loaded(self):
        """Ensure model is loaded"""
        if self.model is None:
            self.model = YOLO(self.model_name)
            self.model.to(self.device)
    
    def process_frame(
        self,
        frame: np.ndarray,
        timestamp: float = 0.0,
        filter_classes: Optional[List[str]] = None
    ) -> DetectionResult:
        """
        Process a single frame to detect objects
        
        Args:
            frame: Input image frame (BGR format)
            timestamp: Frame timestamp in seconds
            filter_classes: Optional list of class names to detect
            
        Returns:
            DetectionResult with detected objects
        """
        self._ensure_model_loaded()
        
        height, width = frame.shape[:2]
        
        # Run inference
        results = self.model(frame, conf=self.confidence_threshold, verbose=False)
        
        # Extract detections
        detected_objects = []
        
        for result in results:
            boxes = result.boxes
            
            for box in boxes:
                # Get box coordinates
                x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                
                # Get class and confidence
                class_id = int(box.cls[0].cpu().numpy())
                confidence = float(box.conf[0].cpu().numpy())
                class_name = result.names[class_id]
                
                # Filter by class if specified
                if filter_classes and class_name not in filter_classes:
                    continue
                
                # Calculate center
                center_x = (x1 + x2) / 2
                center_y = (y1 + y2) / 2
                
                detected_objects.append(DetectedObject(
                    class_id=class_id,
                    class_name=class_name,
                    confidence=confidence,
                    bbox=(int(x1), int(y1), int(x2), int(y2)),
                    center=(center_x, center_y)
                ))
        
        return DetectionResult(
            objects=detected_objects,
            timestamp=timestamp,
            frame_size=(width, height)
        )
    
    def draw_detections(
        self,
        frame: np.ndarray,
        detection_result: DetectionResult,
        show_confidence: bool = True,
        show_center: bool = False
    ) -> np.ndarray:
        """
        Draw detection boxes on frame
        
        Args:
            frame: Input frame
            detection_result: Detection results
            show_confidence: Whether to show confidence scores
            show_center: Whether to show object centers
            
        Returns:
            Annotated frame
        """
        annotated_frame = frame.copy()
        
        for obj in detection_result.objects:
            x1, y1, x2, y2 = obj.bbox
            
            # Draw bounding box
            color = self._get_color_for_class(obj.class_id)
            cv2.rectangle(annotated_frame, (x1, y1), (x2, y2), color, 2)
            
            # Draw label
            label = obj.class_name
            if show_confidence:
                label += f" {obj.confidence:.2f}"
            
            # Get text size for background
            (text_width, text_height), _ = cv2.getTextSize(
                label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 2
            )
            
            # Draw label background
            cv2.rectangle(
                annotated_frame,
                (x1, y1 - text_height - 10),
                (x1 + text_width, y1),
                color,
                -1
            )
            
            # Draw label text
            cv2.putText(
                annotated_frame,
                label,
                (x1, y1 - 5),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (255, 255, 255),
                2
            )
            
            # Draw center point if requested
            if show_center:
                center_x, center_y = obj.center
                cv2.circle(
                    annotated_frame,
                    (int(center_x), int(center_y)),
                    5,
                    color,
                    -1
                )
        
        return annotated_frame
    
    def _get_color_for_class(self, class_id: int) -> Tuple[int, int, int]:
        """
        Get consistent color for a class ID
        
        Args:
            class_id: Class identifier
            
        Returns:
            BGR color tuple
        """
        # Generate deterministic color based on class_id
        np.random.seed(class_id)
        color = tuple(int(c) for c in np.random.randint(0, 255, 3))
        np.random.seed()  # Reset seed
        return color
    
    def get_class_names(self) -> List[str]:
        """
        Get list of detectable class names
        
        Returns:
            List of class names
        """
        self._ensure_model_loaded()
        return list(self.model.names.values())
    
    def calculate_distance_to_object(
        self,
        landmark: 'PoseLandmark',
        detected_object: DetectedObject,
        frame_size: Tuple[int, int]
    ) -> float:
        """
        Calculate normalized distance between a pose landmark and object center
        
        Args:
            landmark: Pose landmark
            detected_object: Detected object
            frame_size: Frame dimensions (width, height)
            
        Returns:
            Normalized Euclidean distance (0-1 range)
        """
        width, height = frame_size
        
        # Convert landmark to pixel coordinates
        lm_x = landmark.x * width
        lm_y = landmark.y * height
        
        # Get object center
        obj_x, obj_y = detected_object.center
        
        # Calculate Euclidean distance
        distance = np.sqrt((lm_x - obj_x)**2 + (lm_y - obj_y)**2)
        
        # Normalize by frame diagonal
        max_distance = np.sqrt(width**2 + height**2)
        normalized_distance = distance / max_distance
        
        return normalized_distance
    
    def check_interaction(
        self,
        landmark: 'PoseLandmark',
        detected_object: DetectedObject,
        frame_size: Tuple[int, int],
        threshold: float = 0.1
    ) -> bool:
        """
        Check if a landmark is interacting with an object
        
        Args:
            landmark: Pose landmark
            detected_object: Detected object
            frame_size: Frame dimensions
            threshold: Distance threshold for interaction (normalized)
            
        Returns:
            True if interaction detected
        """
        distance = self.calculate_distance_to_object(
            landmark, detected_object, frame_size
        )
        return distance < threshold
    
    def get_objects_by_class(
        self,
        detection_result: DetectionResult,
        class_name: str
    ) -> List[DetectedObject]:
        """
        Filter objects by class name
        
        Args:
            detection_result: Detection results
            class_name: Class name to filter
            
        Returns:
            List of objects matching the class
        """
        return [
            obj for obj in detection_result.objects
            if obj.class_name == class_name
        ]
    
    def close(self):
        """Release resources"""
        self.model = None
        self.is_active = False
