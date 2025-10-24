"""
Example: Quick Start Demo
Demonstrates basic usage of Viasion with a simple image
"""

import cv2
import sys
import numpy as np
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from viasion import PoseEstimator, ObjectDetector, InsightsEngine, TrainingDomain


def main():
    """Quick start demo"""
    
    print("="*60)
    print("VIASION QUICK START DEMO")
    print("="*60)
    print("\nThis demo shows how to use Viasion components individually.")
    
    # Create a simple test image (person standing)
    print("\n1. Creating test image...")
    test_image = create_test_image()
    
    # Initialize components
    print("\n2. Initializing Viasion components...")
    pose_estimator = PoseEstimator(min_detection_confidence=0.5)
    object_detector = ObjectDetector(confidence_threshold=0.5)
    insights_engine = InsightsEngine(domain=TrainingDomain.GENERAL.value)
    
    print("   - Pose Estimator: Ready")
    print("   - Object Detector: Ready")
    print("   - Insights Engine: Ready")
    
    # Start analysis session
    print("\n3. Starting analysis session...")
    insights_engine.start_session("demo_session")
    
    # Process the image
    print("\n4. Processing image...")
    pose_data = pose_estimator.process_frame(test_image, timestamp=0.0)
    detection_result = object_detector.process_frame(test_image, timestamp=0.0)
    
    # Display results
    print("\n5. Analysis Results:")
    print("-" * 60)
    
    if pose_data:
        print(f"✓ Pose detected with {pose_data.confidence:.2%} confidence")
        print(f"  Landmarks detected: {len(pose_data.landmarks)}")
        
        # Calculate joint angles
        joint_angles = pose_estimator.get_joint_angles(pose_data)
        print(f"  Joint angles calculated: {len(joint_angles)}")
        
        if joint_angles:
            print("\n  Sample joint angles:")
            for joint, angle in list(joint_angles.items())[:4]:
                print(f"    - {joint}: {angle:.1f}°")
    else:
        print("✗ No pose detected")
    
    print()
    
    if detection_result.objects:
        print(f"✓ {len(detection_result.objects)} object(s) detected:")
        for obj in detection_result.objects[:5]:
            print(f"  - {obj.class_name} ({obj.confidence:.2%})")
    else:
        print("✗ No objects detected")
    
    # Generate insights
    print("\n6. Generating AI insights...")
    if pose_data:
        joint_angles = pose_estimator.get_joint_angles(pose_data)
        insights = insights_engine.analyze_frame(
            pose_data, detection_result, joint_angles
        )
        
        if insights:
            print(f"✓ {len(insights)} insight(s) generated:")
            for insight in insights:
                print(f"  [{insight.category}] {insight.message}")
        else:
            print("✓ No issues detected - good form!")
    
    # Calculate metrics
    print("\n7. Performance Metrics:")
    print("-" * 60)
    metrics = insights_engine.calculate_metrics()
    for name, metric in metrics.items():
        status_symbol = "✓" if metric.status == "good" else "⚠" if metric.status == "warning" else "✗"
        print(f"{status_symbol} {metric.name}: {metric.value:.2f} {metric.unit} [{metric.status}]")
    
    # Get recommendations
    print("\n8. Recommendations:")
    print("-" * 60)
    recommendations = insights_engine.get_recommendations()
    for i, rec in enumerate(recommendations, 1):
        print(f"{i}. {rec}")
    
    # Create visualization
    print("\n9. Creating visualization...")
    annotated = test_image.copy()
    
    if pose_data:
        annotated = pose_estimator.draw_landmarks(annotated, pose_data)
    
    if detection_result.objects:
        annotated = object_detector.draw_detections(annotated, detection_result)
    
    # Save result
    output_path = Path(__file__).parent / "demo_output.jpg"
    cv2.imwrite(str(output_path), annotated)
    print(f"✓ Visualization saved to: {output_path}")
    
    # Cleanup
    print("\n10. Cleaning up...")
    pose_estimator.close()
    object_detector.close()
    insights_engine.reset()
    
    print("\n" + "="*60)
    print("DEMO COMPLETE")
    print("="*60)
    print("\nViasion is ready to transform human motion into AI-driven insights!")
    print("Check out examples/webcam_training.py for a full training session demo.")


def create_test_image():
    """Create a simple test image"""
    # For demo purposes, create a blank image
    # In real use, you would load an actual image or use webcam
    image = np.ones((480, 640, 3), dtype=np.uint8) * 240
    
    # Add some text
    cv2.putText(image, "Viasion Demo", (200, 240),
                cv2.FONT_HERSHEY_SIMPLEX, 2, (50, 50, 50), 3)
    cv2.putText(image, "Load your own image or video", (150, 300),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (80, 80, 80), 2)
    
    return image


if __name__ == "__main__":
    main()
