from setuptools import setup, find_packages

setup(
    name="viasion",
    version="0.1.0",
    description="AI-driven training platform with pose analysis and object detection",
    author="Viasion Team",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    python_requires=">=3.8",
    install_requires=[
        "mediapipe>=0.10.9",
        "opencv-python>=4.9.0",
        "numpy>=1.24.3",
        "tensorflow>=2.15.0",
        "ultralytics>=8.1.0",
        "pandas>=2.0.3",
        "scikit-learn>=1.3.2",
        "matplotlib>=3.7.3",
        "seaborn>=0.13.0",
        "fastapi>=0.109.0",
        "uvicorn>=0.27.0",
        "pydantic>=2.5.3",
        "python-multipart>=0.0.6",
    ],
    extras_require={
        "dev": [
            "pytest>=7.4.3",
            "pytest-asyncio>=0.21.1",
        ],
    },
)
