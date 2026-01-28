"""
Grid-Based Localization Module for Omni

Implements hierarchical region narrowing to find precise click coordinates.
Instead of asking the vision LLM for exact pixels (unreliable), we:
1. Overlay a numbered grid on the screenshot
2. Ask "Which region contains [target]?" (classification - reliable)
3. Zoom into selected region and repeat
4. Return center of final small region

This converts coordinate prediction from regression to classification,
which small vision models handle much better.
"""

import logging
import os
import base64
import tempfile
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
import platform

# Grid configuration
DEFAULT_GRID_SIZE = 3  # 3x3 grid = 9 regions (better resolution)
MAX_ITERATIONS = 5     # 5 iterations * 9x narrowing = very precise
MIN_REGION_SIZE = 30   # Stop when region is smaller than this (pixels)

# Font for grid labels (fallback to default if not found)
if platform.system() == "Windows":
    FONT_PATH = "C:\\Windows\\Fonts\\arial.ttf"
else:
    FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

FALLBACK_FONT_SIZE = 48


def draw_grid_overlay(image: Image.Image, rows: int = 3, cols: int = 3) -> Image.Image:
    """
    Draw a numbered grid overlay on the image.
    
    Args:
        image: PIL Image to overlay grid on
        rows: Number of rows in grid
        cols: Number of columns in grid
    
    Returns:
        New PIL Image with grid overlay
    """
    # Work on a copy
    img = image.copy()
    draw = ImageDraw.Draw(img)
    
    width, height = img.size
    cell_w = width // cols
    cell_h = height // rows
    
    # Load font - dynamic scaling
    # For small regions, make font much smaller to avoid obscuring content
    try:
        # Scale font based on cell size (smaller factor = smaller font)
        # Was // 3, changed to // 4 for better visibility
        target_size = min(cell_w, cell_h) // 4
        target_size = max(10, target_size) # Min size 10
        font = ImageFont.truetype(FONT_PATH, target_size)
        
        # Use simple default font if targeted size is too small for TTF rendering issues
        if target_size < 12:
             font = ImageFont.load_default()
    except:
        try:
            if platform.system() == "Linux":
                font = ImageFont.truetype("/usr/share/fonts/TTF/DejaVuSans-Bold.ttf", FALLBACK_FONT_SIZE)
            else:
                font = ImageFont.load_default()
        except:
            font = ImageFont.load_default()
    
    # Draw grid lines
    line_color = (255, 0, 0)  # Red
    
    # Scale line width - make it visible even after model resizing (usually to ~1120px)
    # Ensure at least 2px width in the model's view
    base_width = max(width, height)
    line_width = max(4, int(base_width / 400)) 
    
    # Vertical lines
    for i in range(1, cols):
        x = i * cell_w
        draw.line([(x, 0), (x, height)], fill=line_color, width=line_width)
    
    # Horizontal lines
    for i in range(1, rows):
        y = i * cell_h
        draw.line([(0, y), (width, y)], fill=line_color, width=line_width)
    
    # Draw numbers in each cell
    region_num = 1
    for row in range(rows):
        for col in range(cols):
            # Calculate center of cell
            cx = col * cell_w + cell_w // 2
            cy = row * cell_h + cell_h // 2
            
            label = str(region_num)
            
            # Get text bounding box for centering
            bbox = draw.textbbox((0, 0), label, font=font)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]
            
            # Draw background circle for visibility
            # Scale circle based on text size
            radius = max(text_w, text_h) // 2 + max(4, line_width) 
            draw.ellipse(
                [cx - radius, cy - radius, cx + radius, cy + radius],
                fill=(255, 255, 255, 200), # More opaque
                outline=(255, 0, 0),
                width=max(2, line_width // 2)
            )
            
            # Draw number
            draw.text(
                (cx - text_w // 2, cy - text_h // 2),
                label,
                fill=(255, 0, 0),
                font=font
            )
            
            region_num += 1
    
    return img


def get_region_bounds(image_size: tuple, region_num: int, rows: int = 3, cols: int = 3) -> tuple:
    """
    Get the pixel bounds of a specific numbered region.
    
    Args:
        image_size: (width, height) of image
        region_num: Region number (1-indexed)
        rows: Grid rows
        cols: Grid columns
    
    Returns:
        (x1, y1, x2, y2) bounds of the region
    """
    width, height = image_size
    cell_w = width // cols
    cell_h = height // rows
    
    # Convert 1-indexed region to 0-indexed row/col
    idx = region_num - 1
    row = idx // cols
    col = idx % cols
    
    x1 = col * cell_w
    y1 = row * cell_h
    x2 = x1 + cell_w
    y2 = y1 + cell_h
    
    # Clamp to image bounds
    x2 = min(x2, width)
    y2 = min(y2, height)
    
    return (x1, y1, x2, y2)
    
def query_llm_for_monitor(llm, monitor_images_b64: list[str], target_description: str) -> int:
    """
    Ask the vision LLM which monitor contains the target.
    
    Returns:
        1-based monitor index, or -1 if not found.
    """
    num_monitors = len(monitor_images_b64)
    prompt = f"""I am showing you {num_monitors} separate monitor screens, arranged one above the other.

TASK: Which of these monitors contains "{target_description}"?

RULES:
1. Prioritize monitors where "{target_description}" is a functional UI BUTTON, icon, or menu label.
2. If "{target_description}" appears inside a code editor or technical text (logs/console), IGNORE it if there is a more likely UI element elsewhere.
3. If one monitor has the browser (e.g., X.com, Twitter), it is the most likely target for UI commands.
4. Output ONLY the number 1 or 2.

- The TOP image is MONITOR 1.
- The BOTTOM image is MONITOR 2.

Respond with ONLY the monitor number (1 or 2). Just the number:"""

    import time
    timestamp = int(time.time() * 1000)
    temp_path = f"/tmp/omni_monitor_query_{timestamp}.png"
    if platform.system() == "Windows":
        temp_path = os.path.join(tempfile.gettempdir(), f"omni_monitor_query_{timestamp}.png")
    
    from PIL import Image, ImageDraw, ImageFont
    import base64
    from io import BytesIO
    
    monitors = [Image.open(BytesIO(base64.b64decode(b64))) for b64 in monitor_images_b64]
    w = monitors[0].width
    h = monitors[0].height
    
    # Use a large font for margins
    try:
         font = ImageFont.truetype(FONT_PATH, 100)
    except:
         font = ImageFont.load_default()
    
    # Stack vertically with white margins for labels
    margin = 150
    stack_h = (h + margin) * num_monitors
    stack = Image.new('RGB', (w, stack_h), (255, 255, 255))
    
    for i, mon in enumerate(monitors):
        y_pos = i * (h + margin)
        
        # Paste the monitor image below the margin
        stack.paste(mon, (0, y_pos + margin))
        
        # Draw label in the white margin above the image
        draw = ImageDraw.Draw(stack)
        label = f"--- MONITOR {i+1} IS BELOW ---"
        
        # Center the label
        bbox = draw.textbbox((0, 0), label, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        draw.text(((w - tw)//2, y_pos + (margin - th)//2), label, fill=(255, 0, 0), font=font)
    
    stack.save(temp_path)
    
    messages = [
        {"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"file://{temp_path}"}}
        ]}
    ]
    
    try:
        output = llm.create_chat_completion(messages=messages, temperature=0.0, max_tokens=32)
        response = output['choices'][0]['message']['content'].strip()
        logging.info(f"Grid Locator: Monitor selection response: '{response}'")
        import re
        numbers = re.findall(r'\d+', response)
        if numbers:
            return int(numbers[0])
    except Exception as e:
        logging.error(f"Grid Locator: Monitor selection failed with error: {e}")
    return -1


def image_to_base64(image: Image.Image) -> str:
    """Convert PIL Image to base64 string."""
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode('utf-8')


def base64_to_image(b64_string: str) -> Image.Image:
    """Convert base64 string to PIL Image."""
    img_data = base64.b64decode(b64_string)
    return Image.open(BytesIO(img_data))


def query_llm_for_region(llm, image_b64: str, target_description: str, rows: int = 3, cols: int = 3) -> int:
    """
    Ask the vision LLM which numbered region contains the target.
    
    Args:
        llm: The llama-cpp-python model instance
        image_b64: Base64 encoded image with grid overlay
        target_description: What we're looking for
        rows: Grid rows
        cols: Grid columns
    
    Returns:
        Region number (1-N), or -1 if not found
    """
    max_region = rows * cols
    
    # Dynamic prompt based on grid shape
    shape_desc = ""
    if rows == 1:
        shape_desc = f"columns (1-{max_region})"
    elif cols == 1:
        shape_desc = f"rows (1-{max_region})"
    else:
        shape_desc = f"regions (1-{max_region})"

    prompt = f"""I have divided this image into {max_region} numbered {shape_desc}.

Look at the RED numbers in WHITE circles - they label each region.

TASK: Find the region containing "{target_description}"

RULES:
- Output ONLY a single number between 1 and {max_region}
- Search for the visible text "{target_description}" or an icon/button representing it.
- If it is a menu item (like in a sidebar), look for the text label next to the icon.
- DO NOT pick a nearby large button (like "Post") if it does not literally match the name.
- The regions are numbered left-to-right, top-to-bottom.

Which region number contains "{target_description}"? Just respond with the number:"""

    # Save unique temp file for the model to prevent llama-cpp caching issues
    import time
    timestamp = int(time.time() * 1000)
    temp_path = f"/tmp/omni_grid_query_{timestamp}.png"
    if platform.system() == "Windows":
        temp_path = os.path.join(tempfile.gettempdir(), f"omni_grid_query_{timestamp}.png")
    
    # Write image data directly (no padding needed now)
    img_data = base64.b64decode(image_b64)
    with open(temp_path, 'wb') as f:
        f.write(img_data)
    
    messages = [
        {"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"file://{temp_path}"}}
        ]}
    ]
    
    try:
        output = llm.create_chat_completion(
            messages=messages,
            max_tokens=32,  # Increased for potentially longer responses
            temperature=0.0
        )
        response = output['choices'][0]['message']['content'].strip()
        
        # Log the full response for debugging
        logging.info(f"Grid Locator: LLM response for '{target_description}': '{response}'")
        
        # Extract number from response
        import re
        numbers = re.findall(r'\d+', response)
        if numbers:
            region = int(numbers[0])
            if 1 <= region <= max_region:
                logging.info(f"Grid Locator: Target '{target_description}' -> Region {region}")
                return region
            else:
                logging.warning(f"Grid Locator: Region {region} out of range (1-{max_region})")
        
        logging.warning(f"Grid Locator: Could not parse valid region from response: {response}")
        return -1
        
    except Exception as e:
        logging.error(f"Grid Locator: LLM query failed: {e}")
        import traceback
        logging.error(traceback.format_exc())
        return -1


def localize_target(
    screenshot_path: str,
    target_description: str,
    llm,
    max_iterations: int = MAX_ITERATIONS,
    grid_size: int = DEFAULT_GRID_SIZE
) -> tuple:
    """
    Main entry point: Find the precise pixel coordinates of a target element.
    
    Uses hierarchical region narrowing:
    1. Draw grid overlay on full screenshot
    2. Ask LLM which region contains target
    3. Crop to that region, repeat
    4. Return center of final small region
    
    Args:
        screenshot_path: Path to the screenshot file
        target_description: What to find (e.g., "the Post button", "glowing sphere")
        llm: Vision-capable llama-cpp-python model instance
        max_iterations: Maximum narrowing iterations
        grid_size: Grid dimensions (3 = 3x3)
    
    Returns:
        (x, y) absolute screen coordinates, or (-1, -1) if not found
    """
    logging.info(f"Grid Locator: Starting localization for '{target_description}'")
    
    # Load original image
    try:
        original_image = Image.open(screenshot_path)
    except Exception as e:
        logging.error(f"Grid Locator: Failed to load screenshot: {e}")
        return (-1, -1)
    
    width, height = original_image.size
    offset_x = 0
    offset_y = 0
    
    current_image = original_image.copy()
    
    # Multi-monitor handling: Isolate target monitor first
    if width > 1.2 * height:
        logging.info(f"Grid Locator: Wide screen detected ({width}x{height}). Isolating monitor first.")
        
        # Standard monitor width is usually around 1920. 
        # For 3840 (Dual 1080p), use 2. For 5120+ (Triple 1080p), use 3.
        num_cols = max(2, round(width / 1920))
        mon_w = width // num_cols
        
        monitors_b64 = []
        for i in range(num_cols):
            mon_img = original_image.crop((i * mon_w, 0, (i+1) * mon_w, height))
            monitors_b64.append(image_to_base64(mon_img))
            
        target_mon = query_llm_for_monitor(llm, monitors_b64, target_description)
        
        if 1 <= target_mon <= num_cols:
            logging.info(f"Grid Locator: Target isolated to Monitor {target_mon}")
            offset_x = (target_mon - 1) * mon_w
            current_image = original_image.crop((offset_x, 0, offset_x + mon_w, height))
        else:
            logging.warning(f"Grid Locator: Monitor isolation failed. Continuing with full screen.")

    for iteration in range(max_iterations):
        width, height = current_image.size
        
        # Check if region is small enough
        if width < MIN_REGION_SIZE or height < MIN_REGION_SIZE:
            logging.info(f"Grid Locator: Region small enough at iteration {iteration}")
            break

        # Draw grid overlay
        grid_image = draw_grid_overlay(current_image, rows=grid_size, cols=grid_size)
        grid_b64 = image_to_base64(grid_image)
        
        # Save debug image
        debug_path = f"/tmp/omni_grid_iter_{iteration}.png"
        if platform.system() == "Windows":
            debug_path = os.path.join(tempfile.gettempdir(), f"omni_grid_iter_{iteration}.png")
        grid_image.save(debug_path)
        logging.info(f"Grid Locator: Saved debug grid to {debug_path}")
        
        # Query LLM
        region = query_llm_for_region(llm, grid_b64, target_description, grid_size, grid_size)
        
        if region == -1:
            logging.warning(f"Grid Locator: LLM failed to identify region at iteration {iteration}")
            # Fall back to center of current region
            break
        
        # Get bounds of selected region
        bounds = get_region_bounds(current_image.size, region, grid_size, grid_size)
        x1, y1, x2, y2 = bounds
        
        # Update cumulative offset
        offset_x += x1
        offset_y += y1
        
        # Crop to selected region for next iteration
        current_image = current_image.crop(bounds)
        
        logging.info(f"Grid Locator: Iteration {iteration}: Region {region} -> Offset ({offset_x}, {offset_y}), Size {current_image.size}")
    
    # Calculate final coordinates (center of final region)
    final_w, final_h = current_image.size
    final_x = offset_x + final_w // 2
    final_y = offset_y + final_h // 2
    
    logging.info(f"Grid Locator: Final coordinates for '{target_description}': ({final_x}, {final_y})")
    
    return (final_x, final_y)


def localize_target_from_b64(
    screenshot_b64: str,
    target_description: str,
    llm,
    max_iterations: int = MAX_ITERATIONS,
    grid_size: int = DEFAULT_GRID_SIZE
) -> tuple:
    """
    Alternative entry point that accepts base64 encoded screenshot.
    
    Args:
        screenshot_b64: Base64 encoded screenshot
        target_description: What to find
        llm: Vision-capable model
        max_iterations: Maximum iterations
        grid_size: Grid dimensions
    
    Returns:
        (x, y) coordinates or (-1, -1) if not found
    """
    # Save to temp file
    temp_path = "/tmp/omni_grid_input.png"
    if platform.system() == "Windows":
        temp_path = os.path.join(tempfile.gettempdir(), "omni_grid_input.png")

    try:
        img_data = base64.b64decode(screenshot_b64)
        with open(temp_path, 'wb') as f:
            f.write(img_data)
    except Exception as e:
        logging.error(f"Grid Locator: Failed to decode screenshot: {e}")
        return (-1, -1)
    
    return localize_target(temp_path, target_description, llm, max_iterations, grid_size)
