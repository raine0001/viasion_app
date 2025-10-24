"""
Domain Configurations Module
Defines training domains and their specific requirements
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Any
from enum import Enum


class TrainingDomain(Enum):
    """Supported training domains"""
    GENERAL = "general"
    SPORTS = "sports"
    SAFETY = "safety"
    REHABILITATION = "rehabilitation"
    MANUFACTURING = "manufacturing"
    FITNESS = "fitness"
    DANCE = "dance"
    MARTIAL_ARTS = "martial_arts"


@dataclass
class ExerciseTemplate:
    """Template for a specific exercise or movement"""
    name: str
    description: str
    target_joints: List[str]
    optimal_angles: Dict[str, tuple]  # joint_name -> (min_angle, max_angle)
    required_objects: List[str]
    duration: Optional[float] = None  # in seconds
    repetitions: Optional[int] = None
    
    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "description": self.description,
            "target_joints": self.target_joints,
            "optimal_angles": self.optimal_angles,
            "required_objects": self.required_objects,
            "duration": self.duration,
            "repetitions": self.repetitions
        }


@dataclass
class DomainConfig:
    """Configuration for a training domain"""
    domain: TrainingDomain
    name: str
    description: str
    key_metrics: List[str]
    exercise_templates: List[ExerciseTemplate]
    safety_rules: List[str]
    performance_thresholds: Dict[str, Any]
    
    def to_dict(self) -> Dict:
        return {
            "domain": self.domain.value,
            "name": self.name,
            "description": self.description,
            "key_metrics": self.key_metrics,
            "exercise_templates": [e.to_dict() for e in self.exercise_templates],
            "safety_rules": self.safety_rules,
            "performance_thresholds": self.performance_thresholds
        }


class DomainConfigFactory:
    """Factory for creating domain configurations"""
    
    @staticmethod
    def create_config(domain: TrainingDomain) -> DomainConfig:
        """
        Create configuration for a specific domain
        
        Args:
            domain: Training domain
            
        Returns:
            DomainConfig object
        """
        configs = {
            TrainingDomain.SPORTS: DomainConfigFactory._create_sports_config(),
            TrainingDomain.SAFETY: DomainConfigFactory._create_safety_config(),
            TrainingDomain.REHABILITATION: DomainConfigFactory._create_rehabilitation_config(),
            TrainingDomain.MANUFACTURING: DomainConfigFactory._create_manufacturing_config(),
            TrainingDomain.FITNESS: DomainConfigFactory._create_fitness_config(),
            TrainingDomain.DANCE: DomainConfigFactory._create_dance_config(),
            TrainingDomain.MARTIAL_ARTS: DomainConfigFactory._create_martial_arts_config(),
            TrainingDomain.GENERAL: DomainConfigFactory._create_general_config(),
        }
        
        return configs.get(domain, DomainConfigFactory._create_general_config())
    
    @staticmethod
    def _create_sports_config() -> DomainConfig:
        """Create sports training configuration"""
        return DomainConfig(
            domain=TrainingDomain.SPORTS,
            name="Sports Training",
            description="Optimize athletic performance through precise movement analysis",
            key_metrics=[
                "movement_speed",
                "form_accuracy",
                "power_generation",
                "balance",
                "coordination"
            ],
            exercise_templates=[
                ExerciseTemplate(
                    name="Squat",
                    description="Proper squat form analysis",
                    target_joints=["left_hip", "right_hip", "left_knee", "right_knee"],
                    optimal_angles={
                        "left_knee": (70, 110),
                        "right_knee": (70, 110),
                        "left_hip": (70, 110),
                        "right_hip": (70, 110)
                    },
                    required_objects=[],
                    repetitions=10
                ),
                ExerciseTemplate(
                    name="Jumping",
                    description="Jump mechanics and landing analysis",
                    target_joints=["left_knee", "right_knee", "left_ankle", "right_ankle"],
                    optimal_angles={
                        "left_knee": (90, 180),
                        "right_knee": (90, 180)
                    },
                    required_objects=[],
                    repetitions=5
                )
            ],
            safety_rules=[
                "Maintain proper knee alignment",
                "Keep back straight during exercises",
                "Land with bent knees to absorb impact",
                "Warm up before intensive movements"
            ],
            performance_thresholds={
                "min_confidence": 0.7,
                "max_angle_deviation": 10.0,
                "stability_threshold": 3.0
            }
        )
    
    @staticmethod
    def _create_safety_config() -> DomainConfig:
        """Create workplace safety configuration"""
        return DomainConfig(
            domain=TrainingDomain.SAFETY,
            name="Workplace Safety",
            description="Ensure safe work practices and prevent injuries",
            key_metrics=[
                "posture_safety",
                "lifting_technique",
                "ergonomics",
                "hazard_awareness"
            ],
            exercise_templates=[
                ExerciseTemplate(
                    name="Proper Lifting",
                    description="Safe lifting technique training",
                    target_joints=["left_hip", "right_hip", "left_knee", "right_knee"],
                    optimal_angles={
                        "left_knee": (70, 110),
                        "right_knee": (70, 110)
                    },
                    required_objects=["box", "package"],
                    repetitions=5
                ),
                ExerciseTemplate(
                    name="Ergonomic Posture",
                    description="Proper standing and working posture",
                    target_joints=["left_shoulder", "right_shoulder", "left_hip", "right_hip"],
                    optimal_angles={
                        "left_shoulder": (160, 180),
                        "right_shoulder": (160, 180)
                    },
                    required_objects=[],
                    duration=60.0
                )
            ],
            safety_rules=[
                "Always bend knees when lifting",
                "Keep load close to body",
                "Avoid twisting while carrying weight",
                "Use proper protective equipment",
                "Maintain neutral spine position"
            ],
            performance_thresholds={
                "min_confidence": 0.7,
                "max_angle_deviation": 20.0,
                "stability_threshold": 5.0
            }
        )
    
    @staticmethod
    def _create_rehabilitation_config() -> DomainConfig:
        """Create rehabilitation configuration"""
        return DomainConfig(
            domain=TrainingDomain.REHABILITATION,
            name="Physical Rehabilitation",
            description="Track recovery progress and ensure proper exercise execution",
            key_metrics=[
                "range_of_motion",
                "movement_control",
                "pain_free_movement",
                "progression_rate"
            ],
            exercise_templates=[
                ExerciseTemplate(
                    name="Knee Flexion",
                    description="Knee range of motion exercise",
                    target_joints=["left_knee", "right_knee"],
                    optimal_angles={
                        "left_knee": (30, 90),
                        "right_knee": (30, 90)
                    },
                    required_objects=[],
                    repetitions=10
                ),
                ExerciseTemplate(
                    name="Shoulder Raise",
                    description="Shoulder mobility exercise",
                    target_joints=["left_shoulder", "right_shoulder"],
                    optimal_angles={
                        "left_shoulder": (90, 180),
                        "right_shoulder": (90, 180)
                    },
                    required_objects=[],
                    repetitions=8
                )
            ],
            safety_rules=[
                "Stop if pain increases",
                "Move slowly and controlled",
                "Stay within prescribed range",
                "Report unusual sensations",
                "Follow therapist guidelines"
            ],
            performance_thresholds={
                "min_confidence": 0.65,
                "max_angle_deviation": 25.0,
                "stability_threshold": 8.0
            }
        )
    
    @staticmethod
    def _create_manufacturing_config() -> DomainConfig:
        """Create manufacturing training configuration"""
        return DomainConfig(
            domain=TrainingDomain.MANUFACTURING,
            name="Manufacturing Operations",
            description="Optimize assembly and manufacturing task efficiency",
            key_metrics=[
                "task_completion_time",
                "motion_efficiency",
                "tool_handling",
                "object_interaction_accuracy"
            ],
            exercise_templates=[
                ExerciseTemplate(
                    name="Part Assembly",
                    description="Precise part assembly training",
                    target_joints=["left_wrist", "right_wrist", "left_elbow", "right_elbow"],
                    optimal_angles={
                        "left_elbow": (70, 120),
                        "right_elbow": (70, 120)
                    },
                    required_objects=["tool", "part"],
                    duration=120.0
                ),
                ExerciseTemplate(
                    name="Material Handling",
                    description="Efficient material movement",
                    target_joints=["left_shoulder", "right_shoulder", "left_elbow", "right_elbow"],
                    optimal_angles={},
                    required_objects=["box", "container"],
                    repetitions=10
                )
            ],
            safety_rules=[
                "Use proper tool grip",
                "Maintain clear work area",
                "Follow ergonomic guidelines",
                "Avoid repetitive strain"
            ],
            performance_thresholds={
                "min_confidence": 0.65,
                "max_angle_deviation": 15.0,
                "interaction_distance": 0.12
            }
        )
    
    @staticmethod
    def _create_fitness_config() -> DomainConfig:
        """Create fitness training configuration"""
        return DomainConfig(
            domain=TrainingDomain.FITNESS,
            name="Fitness Training",
            description="General fitness and exercise form optimization",
            key_metrics=[
                "exercise_form",
                "repetition_count",
                "movement_rhythm",
                "endurance"
            ],
            exercise_templates=[
                ExerciseTemplate(
                    name="Push-up",
                    description="Push-up form analysis",
                    target_joints=["left_elbow", "right_elbow", "left_shoulder", "right_shoulder"],
                    optimal_angles={
                        "left_elbow": (70, 180),
                        "right_elbow": (70, 180)
                    },
                    required_objects=[],
                    repetitions=10
                ),
                ExerciseTemplate(
                    name="Plank",
                    description="Core stability exercise",
                    target_joints=["left_hip", "right_hip", "left_shoulder", "right_shoulder"],
                    optimal_angles={
                        "left_hip": (160, 180),
                        "right_hip": (160, 180)
                    },
                    required_objects=[],
                    duration=30.0
                )
            ],
            safety_rules=[
                "Warm up before exercise",
                "Maintain proper alignment",
                "Breathe consistently",
                "Progress gradually"
            ],
            performance_thresholds={
                "min_confidence": 0.7,
                "max_angle_deviation": 12.0,
                "stability_threshold": 4.0
            }
        )
    
    @staticmethod
    def _create_dance_config() -> DomainConfig:
        """Create dance training configuration"""
        return DomainConfig(
            domain=TrainingDomain.DANCE,
            name="Dance Training",
            description="Improve dance technique and movement quality",
            key_metrics=[
                "movement_fluidity",
                "rhythm_accuracy",
                "posture_quality",
                "expression"
            ],
            exercise_templates=[
                ExerciseTemplate(
                    name="Ballet Position",
                    description="Ballet stance analysis",
                    target_joints=["left_hip", "right_hip", "left_knee", "right_knee"],
                    optimal_angles={},
                    required_objects=[],
                    duration=10.0
                )
            ],
            safety_rules=[
                "Warm up thoroughly",
                "Maintain proper alignment",
                "Avoid overextension",
                "Use proper flooring"
            ],
            performance_thresholds={
                "min_confidence": 0.75,
                "max_angle_deviation": 8.0,
                "stability_threshold": 3.0
            }
        )
    
    @staticmethod
    def _create_martial_arts_config() -> DomainConfig:
        """Create martial arts training configuration"""
        return DomainConfig(
            domain=TrainingDomain.MARTIAL_ARTS,
            name="Martial Arts Training",
            description="Perfect technique and improve combat skills",
            key_metrics=[
                "technique_precision",
                "power_delivery",
                "balance",
                "speed"
            ],
            exercise_templates=[
                ExerciseTemplate(
                    name="Front Kick",
                    description="Front kick technique analysis",
                    target_joints=["left_hip", "left_knee", "right_hip", "right_knee"],
                    optimal_angles={
                        "left_knee": (120, 180),
                        "left_hip": (70, 110)
                    },
                    required_objects=[],
                    repetitions=10
                )
            ],
            safety_rules=[
                "Warm up before practice",
                "Control movements",
                "Maintain proper stance",
                "Use protective equipment"
            ],
            performance_thresholds={
                "min_confidence": 0.7,
                "max_angle_deviation": 10.0,
                "stability_threshold": 4.0
            }
        )
    
    @staticmethod
    def _create_general_config() -> DomainConfig:
        """Create general training configuration"""
        return DomainConfig(
            domain=TrainingDomain.GENERAL,
            name="General Training",
            description="Multi-purpose movement analysis and training",
            key_metrics=[
                "movement_quality",
                "consistency",
                "awareness"
            ],
            exercise_templates=[],
            safety_rules=[
                "Move within comfortable range",
                "Maintain awareness of surroundings",
                "Progress at your own pace"
            ],
            performance_thresholds={
                "min_confidence": 0.6,
                "max_angle_deviation": 15.0,
                "stability_threshold": 5.0
            }
        )
