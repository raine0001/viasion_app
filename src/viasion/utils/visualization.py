"""
Visualization utilities for performance data
"""

import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np
from typing import Dict, List, Optional
from pathlib import Path


def create_performance_dashboard(
    analytics: 'SessionAnalytics',
    save_path: Optional[str] = None,
    show: bool = True
):
    """
    Create a comprehensive performance dashboard
    
    Args:
        analytics: Session analytics data
        save_path: Optional path to save the figure
        show: Whether to display the figure
    """
    fig, axes = plt.subplots(2, 2, figsize=(15, 10))
    fig.suptitle(f'Session Performance Dashboard - {analytics.session_id}', fontsize=16)
    
    # 1. Metrics radar chart (top-left)
    ax = axes[0, 0]
    metrics = [m for m in analytics.metrics.values() if m.unit == "score"]
    
    if metrics:
        labels = [m.name for m in metrics]
        values = [m.value for m in metrics]
        
        angles = np.linspace(0, 2 * np.pi, len(labels), endpoint=False).tolist()
        values += values[:1]
        angles += angles[:1]
        
        ax.plot(angles, values, 'o-', linewidth=2, label='Performance')
        ax.fill(angles, values, alpha=0.25)
        ax.set_xticks(angles[:-1])
        ax.set_xticklabels(labels, size=8)
        ax.set_ylim(0, 100)
        ax.set_title('Performance Metrics')
        ax.grid(True)
    else:
        ax.text(0.5, 0.5, 'No metric scores available', 
                ha='center', va='center', transform=ax.transAxes)
        ax.set_title('Performance Metrics')
    
    # 2. Insights by category (top-right)
    ax = axes[0, 1]
    if analytics.insights:
        categories = {}
        for insight in analytics.insights:
            categories[insight.category] = categories.get(insight.category, 0) + 1
        
        if categories:
            ax.bar(categories.keys(), categories.values(), color='steelblue')
            ax.set_title('Insights by Category')
            ax.set_xlabel('Category')
            ax.set_ylabel('Count')
            plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha='right')
        else:
            ax.text(0.5, 0.5, 'No insights generated', 
                    ha='center', va='center', transform=ax.transAxes)
            ax.set_title('Insights by Category')
    else:
        ax.text(0.5, 0.5, 'No insights generated', 
                ha='center', va='center', transform=ax.transAxes)
        ax.set_title('Insights by Category')
    
    # 3. Insights by severity (bottom-left)
    ax = axes[1, 0]
    if analytics.insights:
        severities = {}
        for insight in analytics.insights:
            severities[insight.severity] = severities.get(insight.severity, 0) + 1
        
        if severities:
            colors = {'info': 'green', 'warning': 'orange', 'critical': 'red'}
            ax.bar(
                severities.keys(),
                severities.values(),
                color=[colors.get(k, 'gray') for k in severities.keys()]
            )
            ax.set_title('Insights by Severity')
            ax.set_xlabel('Severity')
            ax.set_ylabel('Count')
        else:
            ax.text(0.5, 0.5, 'No insights generated', 
                    ha='center', va='center', transform=ax.transAxes)
            ax.set_title('Insights by Severity')
    else:
        ax.text(0.5, 0.5, 'No insights generated', 
                ha='center', va='center', transform=ax.transAxes)
        ax.set_title('Insights by Severity')
    
    # 4. Overall score (bottom-right)
    ax = axes[1, 1]
    score = analytics.score
    
    # Create a gauge-like visualization
    theta = np.linspace(0, np.pi, 100)
    r = np.ones(100)
    
    # Color zones
    ax.fill_between(theta[:33], 0, 1, color='red', alpha=0.3, label='Poor (0-33)')
    ax.fill_between(theta[33:66], 0, 1, color='yellow', alpha=0.3, label='Fair (33-66)')
    ax.fill_between(theta[66:], 0, 1, color='green', alpha=0.3, label='Good (66-100)')
    
    # Score indicator
    score_angle = (score / 100) * np.pi
    ax.plot([score_angle, score_angle], [0, 1], 'b-', linewidth=3, label=f'Score: {score:.1f}')
    
    ax.set_ylim(0, 1)
    ax.set_xlim(0, np.pi)
    ax.set_xticks([0, np.pi/2, np.pi])
    ax.set_xticklabels(['0', '50', '100'])
    ax.set_yticks([])
    ax.set_title('Overall Performance Score')
    ax.legend(loc='upper center', bbox_to_anchor=(0.5, -0.05), ncol=4, fontsize=8)
    
    plt.tight_layout()
    
    if save_path:
        plt.savefig(save_path, dpi=300, bbox_inches='tight')
        print(f"Dashboard saved to: {save_path}")
    
    if show:
        plt.show()
    else:
        plt.close()


def plot_joint_angles_timeline(
    frame_analyses: List['FrameAnalysis'],
    joint_names: Optional[List[str]] = None,
    save_path: Optional[str] = None,
    show: bool = True
):
    """
    Plot joint angles over time
    
    Args:
        frame_analyses: List of frame analysis results
        joint_names: Specific joints to plot (plots all if None)
        save_path: Optional path to save the figure
        show: Whether to display the figure
    """
    if not frame_analyses:
        print("No frame analyses to plot")
        return
    
    # Extract data
    timestamps = [fa.timestamp for fa in frame_analyses]
    
    # Collect all joint names if not specified
    if joint_names is None:
        all_joints = set()
        for fa in frame_analyses:
            all_joints.update(fa.joint_angles.keys())
        joint_names = sorted(list(all_joints))
    
    # Create figure
    n_joints = len(joint_names)
    n_cols = 2
    n_rows = (n_joints + n_cols - 1) // n_cols
    
    fig, axes = plt.subplots(n_rows, n_cols, figsize=(15, 4 * n_rows))
    fig.suptitle('Joint Angles Timeline', fontsize=16)
    
    if n_rows == 1:
        axes = [axes] if n_cols == 1 else axes
    else:
        axes = axes.flatten()
    
    for idx, joint_name in enumerate(joint_names):
        ax = axes[idx]
        
        # Extract angles for this joint
        angles = []
        valid_times = []
        
        for fa in frame_analyses:
            if joint_name in fa.joint_angles:
                angles.append(fa.joint_angles[joint_name])
                valid_times.append(fa.timestamp)
        
        if angles:
            ax.plot(valid_times, angles, marker='o', markersize=2, linewidth=1)
            ax.set_title(joint_name.replace('_', ' ').title())
            ax.set_xlabel('Time (s)')
            ax.set_ylabel('Angle (degrees)')
            ax.grid(True, alpha=0.3)
            
            # Add mean line
            mean_angle = np.mean(angles)
            ax.axhline(mean_angle, color='r', linestyle='--', 
                      label=f'Mean: {mean_angle:.1f}°', alpha=0.5)
            ax.legend(fontsize=8)
        else:
            ax.text(0.5, 0.5, 'No data', ha='center', va='center', transform=ax.transAxes)
            ax.set_title(joint_name.replace('_', ' ').title())
    
    # Hide extra subplots
    for idx in range(len(joint_names), len(axes)):
        axes[idx].set_visible(False)
    
    plt.tight_layout()
    
    if save_path:
        plt.savefig(save_path, dpi=300, bbox_inches='tight')
        print(f"Timeline plot saved to: {save_path}")
    
    if show:
        plt.show()
    else:
        plt.close()
