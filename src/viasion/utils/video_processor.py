"""
Video processing utilities
"""

import cv2
import numpy as np
from typing import Optional, Callable
from pathlib import Path


class VideoProcessor:
    """
    Utility for processing video files with Viasion
    """
    
    def __init__(self, video_path: str):
        """
        Initialize video processor
        
        Args:
            video_path: Path to video file
        """
        self.video_path = video_path
        self.cap = cv2.VideoCapture(video_path)
        
        if not self.cap.isOpened():
            raise ValueError(f"Could not open video file: {video_path}")
        
        # Get video properties
        self.fps = self.cap.get(cv2.CAP_PROP_FPS)
        self.frame_count = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        
        print(f"Video loaded: {Path(video_path).name}")
        print(f"Resolution: {self.width}x{self.height}")
        print(f"FPS: {self.fps:.2f}")
        print(f"Total frames: {self.frame_count}")
    
    def process_video(
        self,
        session_manager: 'SessionManager',
        output_path: Optional[str] = None,
        show_visualization: bool = False,
        frame_callback: Optional[Callable] = None,
        skip_frames: int = 0
    ):
        """
        Process entire video through session manager
        
        Args:
            session_manager: Session manager instance
            output_path: Optional path to save annotated video
            show_visualization: Whether to show real-time visualization
            frame_callback: Optional callback function for each frame
            skip_frames: Number of frames to skip between processing (0 = process all)
        """
        # Setup video writer if output requested
        writer = None
        if output_path:
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            writer = cv2.VideoWriter(
                output_path,
                fourcc,
                self.fps / (skip_frames + 1),
                (self.width, self.height)
            )
        
        frame_number = 0
        processed_frames = 0
        
        try:
            while True:
                ret, frame = self.cap.read()
                if not ret:
                    break
                
                # Skip frames if requested
                if skip_frames > 0 and frame_number % (skip_frames + 1) != 0:
                    frame_number += 1
                    continue
                
                # Calculate timestamp
                timestamp = frame_number / self.fps
                
                # Process frame
                analysis = session_manager.process_frame(
                    frame, processed_frames, timestamp
                )
                
                # Create visualization
                annotated_frame = self._create_visualization(
                    frame, session_manager, analysis
                )
                
                # Call custom callback if provided
                if frame_callback:
                    frame_callback(annotated_frame, analysis)
                
                # Write to output video
                if writer:
                    writer.write(annotated_frame)
                
                # Show visualization
                if show_visualization:
                    cv2.imshow('Viasion Analysis', annotated_frame)
                    if cv2.waitKey(1) & 0xFF == ord('q'):
                        break
                
                processed_frames += 1
                frame_number += 1
                
                # Progress update
                if processed_frames % 30 == 0:
                    progress = (frame_number / self.frame_count) * 100
                    print(f"Processing: {progress:.1f}% ({frame_number}/{self.frame_count})")
        
        finally:
            # Cleanup
            if writer:
                writer.release()
                print(f"Output video saved to: {output_path}")
            
            if show_visualization:
                cv2.destroyAllWindows()
        
        print(f"Processed {processed_frames} frames")
    
    def _create_visualization(
        self,
        frame: np.ndarray,
        session_manager: 'SessionManager',
        analysis: 'FrameAnalysis'
    ) -> np.ndarray:
        """Create annotated visualization of frame"""
        annotated = frame.copy()
        
        # Draw pose if detected
        if analysis.pose_detected:
            pose_data = session_manager.insights_engine.pose_history[-1]
            annotated = session_manager.pose_estimator.draw_landmarks(
                annotated, pose_data
            )
        
        # Draw objects if detected
        if analysis.objects_detected > 0:
            detection_result = session_manager.insights_engine.object_history[-1]
            annotated = session_manager.object_detector.draw_detections(
                annotated, detection_result
            )
        
        # Add information overlay
        annotated = self._add_info_overlay(annotated, session_manager, analysis)
        
        return annotated
    
    def _add_info_overlay(
        self,
        frame: np.ndarray,
        session_manager: 'SessionManager',
        analysis: 'FrameAnalysis'
    ) -> np.ndarray:
        """Add information overlay to frame"""
        overlay = frame.copy()
        h, w = frame.shape[:2]
        
        # Create semi-transparent panel
        panel_height = 150
        cv2.rectangle(overlay, (0, 0), (w, panel_height), (0, 0, 0), -1)
        frame = cv2.addWeighted(overlay, 0.6, frame, 0.4, 0)
        
        # Add text information
        y_offset = 25
        line_height = 25
        
        # Session info
        summary = session_manager.get_session_summary()
        info_lines = [
            f"Session: {summary.get('session_id', 'N/A')}",
            f"Frame: {analysis.frame_number} | Time: {analysis.timestamp:.2f}s",
            f"Pose: {'Detected' if analysis.pose_detected else 'Not detected'} | Objects: {analysis.objects_detected}",
            f"Insights: {summary.get('insights_generated', 0)} | FPS: {summary.get('avg_fps', 0):.1f}",
        ]
        
        for i, line in enumerate(info_lines):
            cv2.putText(
                frame, line,
                (10, y_offset + i * line_height),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6, (255, 255, 255), 2
            )
        
        # Add recent insight if available
        recent_insights = session_manager.get_recent_insights(1)
        if recent_insights:
            insight = recent_insights[0]
            cv2.putText(
                frame,
                f"Latest: {insight['message'][:60]}...",
                (10, y_offset + len(info_lines) * line_height),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5, (0, 255, 255), 1
            )
        
        return frame
    
    def extract_frame(self, frame_number: int) -> Optional[np.ndarray]:
        """
        Extract a specific frame
        
        Args:
            frame_number: Frame index to extract
            
        Returns:
            Frame as numpy array or None
        """
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
        ret, frame = self.cap.read()
        return frame if ret else None
    
    def close(self):
        """Release video capture"""
        if self.cap:
            self.cap.release()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
