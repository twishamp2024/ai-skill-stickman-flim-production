# ai-skill-stickman-flim-production

AI-driven stickman film production system and rendering engine.

## Overview

This repository contains the core animation, geometry, rigging, motion, and rendering engine (`kage_engine`) designed for procedural and scripted 2D/stylized stickman animation and film production.

## Features

- **Rendering Engine (`kage_render`)**:
  - Software rasterizer with supersampling (SSAA)
  - Hardware-accelerated OpenGL rasterizer with MSAA (via GLFW)
  - Frame export to PNG sequences, raw RGB24 video streams (for FFmpeg pipes), and probe metrics
  - Multiple visual styles: `final`, `flat`, `silhouette`
- **Animation & Motion**:
  - Hierarchical skeletal rigging and joint transforms
  - Keyframe interpolation, easing, and procedural motion
  - Collision, posture, and framing probes
  - Event triggers (footsteps, attachments, contacts)
- **JSON Scene Specification**:
  - Declarative scene definition format
  - Frame-by-frame validation and summary analysis

## Building

### Prerequisites

- C++17 compatible compiler (Clang, GCC, or MSVC)
- CMake 3.16 or higher
- Optional: GLFW 3.3+ for hardware OpenGL acceleration

### Build Instructions

```bash
cd engine
mkdir -p build && cd build
cmake ..
cmake --build .
```

### Running Tests

```bash
cd engine/build
ctest --output-on-failure
# or run the test binary directly:
./kage_tests
```

## CLI Usage

```bash
./kage_render SCENE.json [options]
```

Options:
- `--info`: Print scene summary JSON and exit.
- `--validate`: Validate scene and geometry for every frame without rasterization.
- `--backend auto|gl|soft`: Select rasterizer (software or OpenGL).
- `--ssaa N`: Software supersampling factor (default: 3).
- `--msaa N`: OpenGL multisample count (default: 8).
- `--style final|flat|silhouette`: Visual rendering style.
- `--frames A:B`: Frame range `[A, B)`.
- `--frame N`: Render a single frame.
- `--png PATH`: Write the first rendered frame as PNG.
- `--png-dir DIR`: Write sequence as `DIR/frame_NNNNN.png`.
- `--raw PATH|-`: Stream frames as raw rgb24 (for piping to FFmpeg).
- `--probe PATH`: Export per-frame probe JSON metrics.
- `--events PATH`: Export animation events JSON.
