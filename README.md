# Viasion - AI-Driven Training Platform

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Viasion** transforms human motion and object interaction into measurable, AI-driven insights by extending real-time pose analysis and object detection into a multi-domain training ecosystem—helping users improve precision, efficiency, and safety across physical tasks.

## Overview

Viasion is a comprehensive platform that merges pose recognition, object detection, and AI-driven assessment into one adaptive tool for training and evaluating human performance in real time. The platform supports multiple training domains including sports, workplace safety, rehabilitation, manufacturing, fitness, dance, and martial arts.

## Key Features

- **Real-Time Pose Estimation**: Advanced human pose detection and tracking using MediaPipe
- **Object Detection**: Real-time object detection and tracking using YOLO
- **AI-Driven Insights**: Intelligent analysis of movement patterns with actionable feedback
- **Multi-Domain Support**: Pre-configured training domains for various applications
- **Performance Analytics**: Comprehensive metrics and performance tracking
- **Session Management**: Complete training session lifecycle management
- **Safety Focus**: Domain-specific safety rules and guidelines
- **Extensible Architecture**: Modular design for easy customization and extension

## Architecture

```
Viasion Platform
├── Core Modules
│   ├── Pose Estimator (MediaPipe)
│   ├── Object Detector (YOLO)
│   └── Insights Engine (AI Analysis)
├── Training System
│   ├── Session Manager
│   └── Domain Configurations
└── Utilities
    ├── Visualization Tools
    └── Video Processing
```

## Installation

### Prerequisites

- Python 3.8 or higher
- pip package manager
- Webcam or video source (for real-time analysis)

### Install from Source

```bash
# Clone the repository
git clone https://github.com/raine0001/viasion_app.git
cd viasion_app

# Install dependencies
pip install -r requirements.txt

# Install the package
pip install -e .
```

### Quick Install (Dependencies Only)

```bash
pip install mediapipe opencv-python numpy tensorflow ultralytics pandas scikit-learn matplotlib seaborn fastapi uvicorn pydantic
```

## Quick Start

### Basic Usage

```python
from viasion import SessionManager, TrainingDomain
import cv2

# Initialize session manager with a training domain
manager = SessionManager(domain=TrainingDomain.SPORTS)

# Start a training session
session_id = manager.start_session(
    user_id="athlete_01",
    exercise_name="Squat Analysis"
)

# Open video source
cap = cv2.VideoCapture(0)

frame_count = 0
while True:
    ret, frame = cap.read()
    if not ret:
        break
    
    # Process frame through Viasion
    analysis = manager.process_frame(frame, frame_count)
    
    # Get real-time insights
    insights = manager.get_recent_insights(5)
    recommendations = manager.get_recommendations()
    
    frame_count += 1
    
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

# End session and get analytics
analytics = manager.end_session()
print(f"Overall Score: {analytics.score:.1f}/100")

cap.release()
manager.close()
```

### Running Examples

```bash
# Quick start demo
python examples/quick_start.py

# Real-time webcam training
python examples/webcam_training.py
```

## Training Domains

Viasion supports multiple pre-configured training domains:

### 1. Sports Training
- Performance optimization
- Form analysis
- Movement efficiency
- Example exercises: Squats, Jumps, Athletic movements

### 2. Workplace Safety
- Proper lifting techniques
- Ergonomic posture
- Hazard awareness
- Safety compliance monitoring

### 3. Physical Rehabilitation
- Range of motion tracking
- Recovery progress monitoring
- Exercise form verification
- Pain-free movement analysis

### 4. Manufacturing Operations
- Assembly task optimization
- Motion efficiency
- Tool handling analysis
- Ergonomic compliance

### 5. Fitness Training
- Exercise form correction
- Repetition counting
- Movement rhythm analysis
- General fitness improvement

### 6. Dance Training
- Movement fluidity
- Posture quality
- Rhythm accuracy
- Technique refinement

### 7. Martial Arts Training
- Technique precision
- Power delivery
- Balance and stance
- Speed optimization

## Core Components

### Pose Estimator

Real-time human pose detection and tracking:

```python
from viasion import PoseEstimator

estimator = PoseEstimator(min_detection_confidence=0.5)

# Process a frame
pose_data = estimator.process_frame(frame, timestamp=0.0)

# Calculate joint angles
joint_angles = estimator.get_joint_angles(pose_data)

# Visualize pose
annotated_frame = estimator.draw_landmarks(frame, pose_data)
```

### Object Detector

Real-time object detection and tracking:

```python
from viasion import ObjectDetector

detector = ObjectDetector(confidence_threshold=0.5)

# Detect objects
detection_result = detector.process_frame(frame, timestamp=0.0)

# Filter by class
balls = detector.get_objects_by_class(detection_result, "sports ball")

# Visualize detections
annotated_frame = detector.draw_detections(frame, detection_result)
```

### Insights Engine

AI-driven performance analysis:

```python
from viasion import InsightsEngine

engine = InsightsEngine(domain="sports")
engine.start_session("session_001")

# Analyze frame
insights = engine.analyze_frame(pose_data, detection_result, joint_angles)

# Get metrics
metrics = engine.calculate_metrics()

# Get recommendations
recommendations = engine.get_recommendations()
```

## Performance Metrics

Viasion tracks multiple performance metrics:

- **Confidence Score**: Pose detection quality
- **Movement Consistency**: Stability of movements
- **Frame Rate**: Processing performance
- **Object Interactions**: Interaction tracking
- **Joint Angles**: Biomechanical analysis
- **Form Accuracy**: Movement quality assessment

## API Documentation

### SessionManager

Main interface for managing training sessions.

**Methods:**
- `start_session(session_id, user_id, exercise_name)`: Start a new session
- `process_frame(frame, frame_number, timestamp)`: Process a video frame
- `end_session()`: End session and generate analytics
- `get_current_metrics()`: Get real-time metrics
- `get_recent_insights(count)`: Get recent insights
- `get_recommendations()`: Get actionable recommendations
- `get_domain_info()`: Get domain configuration
- `get_session_summary()`: Get session summary

### Training Domains

Available domains: `SPORTS`, `SAFETY`, `REHABILITATION`, `MANUFACTURING`, `FITNESS`, `DANCE`, `MARTIAL_ARTS`, `GENERAL`

## Data Storage

Sessions can be automatically saved to disk:

```python
manager.start_session(
    session_id="workout_001",
    save_data=True,
    data_directory="./data/sessions"
)
```

Session data includes:
- Configuration
- Analytics
- Frame-by-frame analysis
- Insights and recommendations
- Performance metrics

## Testing

Run the test suite:

```bash
# Install test dependencies
pip install pytest pytest-asyncio

# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_core.py -v
pytest tests/test_training.py -v
```

## Performance Considerations

- **GPU Acceleration**: Use CUDA-enabled devices for faster processing
- **Frame Skipping**: Process every Nth frame for better real-time performance
- **Model Selection**: Choose appropriate YOLO model size (yolov8n for speed, yolov8x for accuracy)
- **Resolution**: Lower input resolution for faster processing

## Use Cases

### 1. Athletic Training
Monitor and improve athletic performance with precise movement analysis.

### 2. Workplace Safety
Ensure safe work practices and prevent injuries in industrial settings.

### 3. Physical Therapy
Track rehabilitation progress and ensure proper exercise execution.

### 4. Ergonomics Assessment
Evaluate and improve workplace ergonomics.

### 5. Fitness Coaching
Provide real-time form feedback for exercise routines.

### 6. Manufacturing Training
Optimize assembly processes and reduce repetitive strain.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Citation

If you use Viasion in your research or project, please cite:

```bibtex
@software{viasion2024,
  title={Viasion: AI-Driven Training Platform},
  author={Viasion Team},
  year={2024},
  url={https://github.com/raine0001/viasion_app}
}
```

## Acknowledgments

- MediaPipe for pose estimation
- Ultralytics YOLO for object detection
- OpenCV for computer vision utilities

## Support

For issues, questions, or contributions, please visit:
- GitHub Issues: https://github.com/raine0001/viasion_app/issues

## Roadmap

- [ ] Web API interface
- [ ] Mobile application support
- [ ] Advanced exercise templates
- [ ] Multi-person tracking
- [ ] Cloud-based analytics
- [ ] Integration with wearable devices
- [ ] Real-time coaching interface
- [ ] 3D pose visualization
