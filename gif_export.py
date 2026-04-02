"""
GIF Export for MoveMusic Save Editor

Exports orbit animations as GIF files by capturing OpenGL framebuffer frames.
Reuses the same orbit camera logic as GLB export for consistency.
"""

from __future__ import annotations

import math
import os
import logging
from typing import Optional

try:
    from PIL import Image
    import imageio
    import numpy as np
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    from OpenGL.GL import *
except ImportError:
    pass

from model import Project
from viewport3d import SceneViewport, OrbitCamera

logger = logging.getLogger(__name__)


class GifExportError(Exception):
    """Base exception for GIF export operations."""
    pass


def check_dependencies():
    """Check if required dependencies are available."""
    if not PIL_AVAILABLE:
        raise GifExportError(
            "Required dependencies not installed. Please install:\n"
            "pip install pillow imageio[ffmpeg] numpy"
        )


def export_orbit_gif(project: Project, filepath: str, viewport: SceneViewport,
                    duration: float = 4.0, fps: int = 15, size: tuple = (800, 600)) -> bool:
    """
    Export project as orbit animation GIF.

    Args:
        project: Project to export
        filepath: Output GIF file path
        viewport: Viewport3D instance for rendering
        duration: Animation duration in seconds (default: 4.0)
        fps: Frames per second (default: 15)
        size: Output resolution as (width, height) (default: 800x600)

    Returns:
        True if successful, False otherwise

    Raises:
        GifExportError: If export fails
    """
    check_dependencies()

    if not project.elements:
        raise GifExportError("Cannot export empty project")

    try:
        # Calculate number of frames
        num_frames = int(duration * fps)
        if num_frames < 2:
            num_frames = 2

        # Setup camera for orbit animation
        original_camera = _backup_camera(viewport.camera)
        orbit_params = _calculate_orbit_parameters(project)

        # Create temporary camera for animation
        anim_camera = _create_animation_camera(orbit_params)

        # Capture frames
        frames = []
        logger.info(f"Capturing {num_frames} frames for GIF export...")

        for frame_idx in range(num_frames):
            # Calculate orbit position for this frame
            progress = frame_idx / (num_frames - 1) if num_frames > 1 else 0.0
            angle = 2 * math.pi * progress

            # Position camera along orbit
            _position_camera_for_frame(anim_camera, orbit_params, angle)

            # Replace viewport camera temporarily
            viewport.camera = anim_camera

            # Capture frame
            frame_data = _capture_frame(viewport, size)
            if frame_data is not None:
                frames.append(frame_data)
            else:
                logger.warning(f"Failed to capture frame {frame_idx}")

        # Restore original camera
        viewport.camera = original_camera

        if not frames:
            raise GifExportError("Failed to capture any frames")

        # Generate GIF
        _save_gif(frames, filepath, fps)

        logger.info(f"Successfully exported {len(frames)} frame GIF to {filepath}")
        return True

    except Exception as e:
        # Restore camera on any error
        if 'original_camera' in locals():
            viewport.camera = original_camera
        logger.error(f"GIF export failed: {str(e)}")
        raise GifExportError(f"Failed to export GIF: {str(e)}")


def _backup_camera(camera: OrbitCamera) -> OrbitCamera:
    """Create a backup copy of the current camera state."""
    backup = OrbitCamera()
    backup.target = camera.target.copy()
    backup.distance = camera.distance
    backup.yaw = camera.yaw
    backup.pitch = camera.pitch
    backup.fov = camera.fov
    backup.near = camera.near
    backup.far = camera.far
    backup.ortho = camera.ortho
    return backup


def _calculate_orbit_parameters(project: Project) -> dict:
    """Calculate orbit parameters using same logic as GLB export."""
    # Calculate scene centroid (center of all elements)
    if not project.elements:
        return {
            'center': [0.0, 0.0, 0.0],
            'orbit_radius': 100.0
        }

    cx = sum(e.transform.translation.x for e in project.elements) / len(project.elements)
    cy = sum(e.transform.translation.y for e in project.elements) / len(project.elements)
    cz = sum(e.transform.translation.z for e in project.elements) / len(project.elements)

    # Find maximum distance from centroid to any element
    max_dist = 0.0
    for element in project.elements:
        pos = element.transform.translation
        distance = math.sqrt((pos.x - cx)**2 + (pos.y - cy)**2 + (pos.z - cz)**2)
        max_dist = max(max_dist, distance)

    # Set orbit radius (same logic as export3d.py)
    orbit_radius = max(max_dist * 2, 100.0)

    return {
        'center': [cx, cy, cz],
        'orbit_radius': orbit_radius
    }


def _create_animation_camera(orbit_params: dict) -> OrbitCamera:
    """Create camera for animation with appropriate settings."""
    camera = OrbitCamera()
    camera.target = orbit_params['center']
    camera.distance = orbit_params['orbit_radius']
    camera.pitch = 20.0  # Slight downward angle
    camera.fov = 50.0    # Match viewport default
    camera.ortho = False # Perspective view for better 3D effect
    return camera


def _position_camera_for_frame(camera: OrbitCamera, orbit_params: dict, angle: float):
    """Position camera for specific frame in orbit animation."""
    cx, cy, cz = orbit_params['center']
    radius = orbit_params['orbit_radius']

    # Calculate orbit position (same as export3d.py)
    # Camera orbits in XY plane around the centroid
    cam_x = cx + radius * math.cos(angle)
    cam_y = cy + radius * math.sin(angle)
    cam_z = cz + radius * 0.3  # Slightly elevated

    # Update camera to look at centroid from orbit position
    camera.target = [cx, cy, cz]

    # Calculate camera distance and angles to achieve desired position
    # For OrbitCamera, we need to work backwards from desired eye position
    dx = cam_x - cx
    dy = cam_y - cy
    dz = cam_z - cz

    camera.distance = math.sqrt(dx*dx + dy*dy + dz*dz)
    camera.yaw = math.degrees(math.atan2(dy, dx))
    camera.pitch = math.degrees(math.asin(dz / camera.distance)) if camera.distance > 0 else 0.0


def _capture_frame(viewport: Viewport3D, size: tuple) -> Optional[np.ndarray]:
    """Capture a single frame from the OpenGL viewport."""
    try:
        width, height = size

        # Force viewport to render at desired size
        original_size = (viewport.width(), viewport.height())
        viewport.resize(width, height)

        # Make sure OpenGL context is current
        viewport.makeCurrent()

        # Force a repaint
        viewport.paintGL()

        # Read framebuffer
        glReadBuffer(GL_FRONT)

        # Read pixels as RGB
        pixel_data = glReadPixels(0, 0, width, height, GL_RGB, GL_UNSIGNED_BYTE)

        # Convert to numpy array and flip vertically (OpenGL has origin at bottom-left)
        image_array = np.frombuffer(pixel_data, dtype=np.uint8)
        image_array = image_array.reshape((height, width, 3))
        image_array = np.flipud(image_array)  # Flip Y axis

        # Restore original viewport size
        viewport.resize(*original_size)

        return image_array

    except Exception as e:
        logger.error(f"Failed to capture frame: {e}")
        # Restore original size on error
        if 'original_size' in locals():
            viewport.resize(*original_size)
        return None


def _save_gif(frames: list, filepath: str, fps: int):
    """Save captured frames as optimized GIF."""
    try:
        # Convert numpy arrays to PIL Images
        pil_images = []
        for frame in frames:
            img = Image.fromarray(frame, mode='RGB')
            # Convert to P mode (palette) for smaller file size
            img = img.convert('P', palette=Image.ADAPTIVE, colors=256)
            pil_images.append(img)

        # Calculate frame duration in milliseconds
        frame_duration = 1000.0 / fps

        # Save as GIF with optimization
        imageio.mimsave(
            filepath,
            pil_images,
            duration=frame_duration / 1000.0,  # imageio expects seconds
            loop=0,  # Infinite loop
            optimize=True,  # Optimize for smaller file size
            quality=85  # Good quality/size balance
        )

        # Log file size
        file_size = os.path.getsize(filepath)
        logger.info(f"GIF saved: {file_size:,} bytes ({file_size/1024/1024:.1f} MB)")

    except Exception as e:
        raise GifExportError(f"Failed to save GIF: {str(e)}")


def export_still_image(project: Project, filepath: str, viewport: SceneViewport,
                      size: tuple = (1920, 1080)) -> bool:
    """
    Export a single high-resolution still image of the project.

    Args:
        project: Project to export
        filepath: Output image file path (PNG, JPG, etc.)
        viewport: Viewport3D instance for rendering
        size: Output resolution as (width, height)

    Returns:
        True if successful, False otherwise
    """
    check_dependencies()

    if not project.elements:
        raise GifExportError("Cannot export empty project")

    try:
        # Capture single frame at high resolution
        frame_data = _capture_frame(viewport, size)

        if frame_data is None:
            raise GifExportError("Failed to capture frame")

        # Save as image
        img = Image.fromarray(frame_data, mode='RGB')
        img.save(filepath, quality=95, optimize=True)

        logger.info(f"Still image exported to {filepath}")
        return True

    except Exception as e:
        logger.error(f"Still image export failed: {str(e)}")
        raise GifExportError(f"Failed to export image: {str(e)}")


# Utility functions for menu integration
def get_supported_gif_formats():
    """Get list of supported GIF export formats."""
    return [
        ("Animated GIF", "*.gif"),
        ("All Files", "*.*")
    ]


def get_supported_image_formats():
    """Get list of supported still image formats."""
    return [
        ("PNG Image", "*.png"),
        ("JPEG Image", "*.jpg"),
        ("TIFF Image", "*.tiff"),
        ("All Files", "*.*")
    ]