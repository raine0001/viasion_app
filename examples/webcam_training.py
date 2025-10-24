"""
Example: Webcam Real-Time Training Session
Demonstrates live pose estimation and object detection with real-time feedback
"""

import cv2
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from viasion import SessionManager, TrainingDomain


def main():
    """Run real-time training session from webcam"""
    
    # Initialize session manager with sports domain
    print("Initializing Viasion...")
    session_manager = SessionManager(
        domain=TrainingDomain.SPORTS,
        pose_config={"min_detection_confidence": 0.5},
        detector_config={"confidence_threshold": 0.5}
    )
    
    # Display domain information
    domain_info = session_manager.get_domain_info()
    print(f"\nDomain: {domain_info['name']}")
    print(f"Description: {domain_info['description']}")
    print(f"\nSafety Rules:")
    for rule in domain_info['safety_rules']:
        print(f"  - {rule}")
    
    # Open webcam
    print("\nOpening webcam...")
    cap = cv2.VideoCapture(0)
    
    if not cap.isOpened():
        print("Error: Could not open webcam")
        return
    
    # Set resolution
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    
    # Start training session
    print("\nStarting training session...")
    session_id = session_manager.start_session(
        user_id="demo_user",
        exercise_name="Squat Practice"
    )
    
    print("\nControls:")
    print("  - Press 'q' to quit")
    print("  - Press 's' to show summary")
    print("  - Press 'r' to show recommendations")
    print("\n" + "="*50)
    
    frame_count = 0
    
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                print("Error reading frame")
                break
            
            # Process frame
            analysis = session_manager.process_frame(frame, frame_count)
            
            # Create visualization
            annotated_frame = frame.copy()
            
            # Draw pose if detected
            if analysis.pose_detected:
                pose_data = session_manager.insights_engine.pose_history[-1]
                annotated_frame = session_manager.pose_estimator.draw_landmarks(
                    annotated_frame, pose_data
                )
            
            # Draw objects if detected
            if analysis.objects_detected > 0:
                detection_result = session_manager.insights_engine.object_history[-1]
                annotated_frame = session_manager.object_detector.draw_detections(
                    annotated_frame, detection_result
                )
            
            # Add info overlay
            annotated_frame = add_info_overlay(
                annotated_frame, session_manager, analysis
            )
            
            # Display frame
            cv2.imshow('Viasion Training', annotated_frame)
            
            # Handle key presses
            key = cv2.waitKey(1) & 0xFF
            
            if key == ord('q'):
                print("\nEnding session...")
                break
            elif key == ord('s'):
                show_summary(session_manager)
            elif key == ord('r'):
                show_recommendations(session_manager)
            
            frame_count += 1
    
    finally:
        # End session and get analytics
        analytics = session_manager.end_session()
        
        # Display final summary
        print("\n" + "="*50)
        print("SESSION COMPLETE")
        print("="*50)
        print(f"\nSession ID: {analytics.session_id}")
        print(f"Duration: {analytics.duration:.2f} seconds")
        print(f"Overall Score: {analytics.score:.1f}/100")
        
        print(f"\nMetrics:")
        for name, metric in analytics.metrics.items():
            print(f"  {metric.name}: {metric.value:.2f} {metric.unit} [{metric.status}]")
        
        print(f"\nKey Insights ({len(analytics.insights)} total):")
        for insight in analytics.insights[:5]:
            print(f"  [{insight.severity.upper()}] {insight.message}")
        
        print(f"\nRecommendations:")
        recommendations = session_manager.get_recommendations()
        for i, rec in enumerate(recommendations, 1):
            print(f"  {i}. {rec}")
        
        if analytics.improvement_areas:
            print(f"\nAreas for Improvement:")
            for area in analytics.improvement_areas:
                print(f"  - {area}")
        
        # Cleanup
        cap.release()
        cv2.destroyAllWindows()
        session_manager.close()


def add_info_overlay(frame, session_manager, analysis):
    """Add information overlay to frame"""
    overlay = frame.copy()
    h, w = frame.shape[:2]
    
    # Create semi-transparent panel at top
    panel_height = 180
    cv2.rectangle(overlay, (0, 0), (w, panel_height), (0, 0, 0), -1)
    frame = cv2.addWeighted(overlay, 0.6, frame, 0.4, 0)
    
    # Get session summary
    summary = session_manager.get_session_summary()
    
    # Add text
    y = 25
    line_h = 25
    
    lines = [
        f"Session: {summary['session_id']}",
        f"Frame: {analysis.frame_number} | Time: {analysis.timestamp:.2f}s",
        f"Pose: {'DETECTED' if analysis.pose_detected else 'NOT DETECTED'} | Objects: {analysis.objects_detected}",
        f"Insights: {summary['insights_generated']} | FPS: {summary['avg_fps']:.1f}",
        "",
        "Press 'q' to quit | 's' for summary | 'r' for recommendations"
    ]
    
    for i, line in enumerate(lines):
        cv2.putText(frame, line, (10, y + i * line_h),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    
    # Add recent insight
    recent = session_manager.get_recent_insights(1)
    if recent:
        insight = recent[0]
        color = (0, 255, 0) if insight['severity'] == 'info' else \
                (0, 165, 255) if insight['severity'] == 'warning' else (0, 0, 255)
        
        msg = insight['message']
        if len(msg) > 80:
            msg = msg[:77] + "..."
        
        cv2.putText(frame, f"Latest: {msg}",
                   (10, y + len(lines) * line_h),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
    
    return frame


def show_summary(session_manager):
    """Print current session summary"""
    summary = session_manager.get_session_summary()
    print("\n" + "-"*40)
    print("CURRENT SESSION SUMMARY")
    print("-"*40)
    for key, value in summary.items():
        print(f"{key}: {value}")
    print("-"*40 + "\n")


def show_recommendations(session_manager):
    """Print current recommendations"""
    recs = session_manager.get_recommendations()
    print("\n" + "-"*40)
    print("RECOMMENDATIONS")
    print("-"*40)
    for i, rec in enumerate(recs, 1):
        print(f"{i}. {rec}")
    print("-"*40 + "\n")


if __name__ == "__main__":
    main()
