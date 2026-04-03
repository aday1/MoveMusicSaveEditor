"""
GIF Export for MoveMusic Save Editor

Exports orbit animations as GIF files by capturing OpenGL framebuffer frames.
Reuses the same orbit camera logic as GLB export for consistency.
"""

from __future__ import annotations

import math
import os
import logging
from typing import Callable, Optional

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
                    duration: float = 4.0, fps: int = 15, size: tuple = (800, 600),
                    clockwise: bool = True, turns: float = 1.0, elevation_factor: float = 0.3,
                    palette_colors: int = 256, dither: bool = True,
                    progress_callback: Optional[Callable[[int, int, str], bool]] = None) -> bool:
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
        frames = _capture_orbit_frames(
            project,
            viewport,
            duration=duration,
            fps=fps,
            size=size,
            clockwise=clockwise,
            turns=turns,
            elevation_factor=elevation_factor,
            progress_callback=progress_callback,
            progress_stage="Capturing GIF frames",
        )

        total_frames = max(2, int(duration * fps))
        if progress_callback and not progress_callback(total_frames, total_frames, "Encoding GIF"):
            raise GifExportError("Export canceled")

        if not frames:
            raise GifExportError("Failed to capture any frames")

        # Generate GIF
        _save_gif(frames, filepath, fps, palette_colors=palette_colors, dither=dither)

        logger.info(f"Successfully exported {len(frames)} frame GIF to {filepath}")
        return True

    except Exception as e:
        logger.error(f"GIF export failed: {str(e)}")
        raise GifExportError(f"Failed to export GIF: {str(e)}")


def export_orbit_mp4(project: Project, filepath: str, viewport: SceneViewport,
                    duration: float = 4.0, fps: int = 30, size: tuple = (1280, 720),
                    clockwise: bool = True, turns: float = 1.0, elevation_factor: float = 0.3,
                    progress_callback: Optional[Callable[[int, int, str], bool]] = None) -> bool:
    """Export project as orbit animation MP4 using imageio/ffmpeg."""
    check_dependencies()

    if not project.elements:
        raise GifExportError("Cannot export empty project")

    try:
        frames = _capture_orbit_frames(
            project,
            viewport,
            duration=duration,
            fps=fps,
            size=size,
            clockwise=clockwise,
            turns=turns,
            elevation_factor=elevation_factor,
            progress_callback=progress_callback,
            progress_stage="Capturing MP4 frames",
        )

        total_frames = max(2, int(duration * fps))
        if progress_callback and not progress_callback(total_frames, total_frames, "Encoding MP4"):
            raise GifExportError("Export canceled")

        _save_mp4(frames, filepath, fps)
        logger.info(f"Successfully exported {len(frames)} frame MP4 to {filepath}")
        return True
    except Exception as e:
        logger.error(f"MP4 export failed: {str(e)}")
        raise GifExportError(f"Failed to export MP4: {str(e)}")


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


def _capture_orbit_frames(project: Project, viewport: SceneViewport,
                          duration: float, fps: int, size: tuple,
                          clockwise: bool, turns: float, elevation_factor: float,
                          progress_callback: Optional[Callable[[int, int, str], bool]],
                          progress_stage: str) -> list:
    """Capture orbit animation frames with configurable orbit behavior."""
    num_frames = max(2, int(duration * fps))
    turns = max(0.1, float(turns))

    original_camera = _backup_camera(viewport.camera)
    orbit_params = _calculate_orbit_parameters(project)
    orbit_params['elevation_factor'] = max(-1.5, min(1.5, float(elevation_factor)))
    anim_camera = _create_animation_camera(orbit_params)

    frames = []
    logger.info(f"Capturing {num_frames} frames for orbit export...")
    try:
        for frame_idx in range(num_frames):
            if progress_callback and not progress_callback(frame_idx, num_frames, progress_stage):
                raise GifExportError("Export canceled")

            progress = frame_idx / (num_frames - 1) if num_frames > 1 else 0.0
            angle = 2 * math.pi * turns * progress
            if clockwise:
                angle = -angle

            _position_camera_for_frame(anim_camera, orbit_params, angle)
            viewport.camera = anim_camera

            frame_data = _capture_frame(viewport, size)
            if frame_data is not None:
                frames.append(frame_data)
            else:
                logger.warning(f"Failed to capture frame {frame_idx}")
    finally:
        viewport.camera = original_camera

    return frames


def _position_camera_for_frame(camera: OrbitCamera, orbit_params: dict, angle: float):
    """Position camera for specific frame in orbit animation."""
    cx, cy, cz = orbit_params['center']
    radius = orbit_params['orbit_radius']

    # Calculate orbit position (same as export3d.py)
    # Camera orbits in XY plane around the centroid
    cam_x = cx + radius * math.cos(angle)
    cam_y = cy + radius * math.sin(angle)
    elev = orbit_params.get('elevation_factor', 0.3)
    cam_z = cz + radius * elev  # configurable elevation

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


def _capture_frame(viewport, size: tuple) -> Optional['np.ndarray']:
    """Capture a single frame from the OpenGL viewport using Qt's grabFramebuffer."""
    try:
        from PyQt6.QtWidgets import QApplication
        QApplication.processEvents()

        width, height = size

        # Trigger a render pass with the animation camera already set
        viewport.makeCurrent()
        viewport.paintGL()
        viewport.doneCurrent()

        # grabFramebuffer reads from the back-buffer — safe on all platforms
        qimage = viewport.grabFramebuffer()
        if qimage.isNull():
            logger.warning("grabFramebuffer returned null image")
            return None

        # Scale to desired output size
        from PyQt6.QtCore import Qt as _Qt
        qimage = qimage.scaled(width, height, _Qt.AspectRatioMode.IgnoreAspectRatio,
                               _Qt.TransformationMode.SmoothTransformation)

        # Convert QImage to numpy RGB array
        qimage = qimage.convertToFormat(qimage.Format.Format_RGB888)
        ptr = qimage.bits()
        ptr.setsize(qimage.sizeInBytes())
        image_array = np.frombuffer(ptr, dtype=np.uint8).reshape((qimage.height(), qimage.width(), 3)).copy()
        return image_array

    except Exception as e:
        logger.error(f"Failed to capture frame: {e}")
        return None


def _save_gif(frames: list, filepath: str, fps: int, palette_colors: int = 256, dither: bool = True):
    """Save captured frames as optimized GIF."""
    try:
        palette_colors = max(2, min(256, int(palette_colors)))
        # Convert numpy arrays to PIL Images
        pil_images = []
        for frame in frames:
            img = Image.fromarray(frame, mode='RGB')
            # Convert to palette mode with user-defined color richness.
            dither_mode = Image.Dither.FLOYDSTEINBERG if dither else Image.Dither.NONE
            img = img.convert('P', palette=Image.ADAPTIVE, colors=palette_colors, dither=dither_mode)
            pil_images.append(img)

        # Calculate frame duration in milliseconds
        frame_duration = 1000.0 / fps

        # Save as GIF with optimization
        imageio.mimsave(
            filepath,
            pil_images,
            duration=frame_duration / 1000.0,  # imageio expects seconds
            loop=0,  # Infinite loop
        )

        # Log file size
        file_size = os.path.getsize(filepath)
        logger.info(f"GIF saved: {file_size:,} bytes ({file_size/1024/1024:.1f} MB)")

    except Exception as e:
        raise GifExportError(f"Failed to save GIF: {str(e)}")


def _save_mp4(frames: list, filepath: str, fps: int):
    """Save frames to MP4 using ffmpeg via imageio."""
    try:
        if not frames:
            raise GifExportError("No frames captured for MP4 export")
        # Ensure even dimensions for H.264 encoders.
        h, w = frames[0].shape[:2]
        if w % 2 != 0 or h % 2 != 0:
            frames = [f[:h - (h % 2), :w - (w % 2)] for f in frames]

        imageio.mimsave(
            filepath,
            frames,
            fps=max(1, int(fps)),
            codec="libx264",
            quality=8,
            pixelformat="yuv420p",
        )

        file_size = os.path.getsize(filepath)
        logger.info(f"MP4 saved: {file_size:,} bytes ({file_size/1024/1024:.1f} MB)")
    except Exception as e:
        raise GifExportError(f"Failed to save MP4: {str(e)}")


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