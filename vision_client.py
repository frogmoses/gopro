"""Claude Vision API client — sends images for AI analysis.

Encodes an image as base64, sends it to Claude with a text prompt,
and returns the model's text response.
"""

import base64
import os
from pathlib import Path

import anthropic


# Model to use for vision queries
VISION_MODEL = "claude-sonnet-4-5-20250929"

# Max tokens for response
MAX_TOKENS = 1024


def _encode_image(image_path):
    """Read and base64-encode an image file.

    Args:
        image_path: Path to a JPEG or PNG image.

    Returns:
        Tuple of (base64_data, media_type).
    """
    path = Path(image_path)
    data = path.read_bytes()
    b64 = base64.standard_b64encode(data).decode("utf-8")

    # Determine media type from extension
    ext = path.suffix.lower()
    media_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    media_type = media_types.get(ext, "image/jpeg")

    return b64, media_type


def ask_about_image(image_path, question, system_prompt=None):
    """Send an image to Claude Vision with a question.

    Args:
        image_path: Path to the image file.
        question: Text question about the image.
        system_prompt: Optional system prompt for context.

    Returns:
        Claude's text response, or None on error.
    """
    # Verify API key is available (injected by run_r1-eye wrapper)
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Error: ANTHROPIC_API_KEY not set. Run via run_r1-eye wrapper.")
        return None

    # Encode the image
    b64_data, media_type = _encode_image(image_path)

    # Build the message with image + text
    client = anthropic.Anthropic()

    kwargs = {
        "model": VISION_MODEL,
        "max_tokens": MAX_TOKENS,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": b64_data,
                        },
                    },
                    {
                        "type": "text",
                        "text": question,
                    },
                ],
            }
        ],
    }

    # Add system prompt if provided
    if system_prompt:
        kwargs["system"] = system_prompt

    try:
        response = client.messages.create(**kwargs)
        # Extract text from response
        text_blocks = [b.text for b in response.content if b.type == "text"]
        return "\n".join(text_blocks)
    except anthropic.APIError as e:
        print(f"API error: {e}")
        return None


def assess_roast_color(image_path, elapsed_seconds=None, phase=None):
    """Specialized vision query for roast bean color assessment.

    Used by the sentinel during roast monitoring. Returns structured
    color and development observations.

    Args:
        image_path: Path to the bean image.
        elapsed_seconds: Seconds since CHARGE (for context).
        phase: Current roast phase (drying/maillard/development).

    Returns:
        Dict with color_assessment, development_score, uniformity.
    """
    # Build context-aware system prompt with roasting domain knowledge
    system_parts = [
        "You are an expert coffee roast analyst monitoring a live roast.",
        "You assess bean color to guide the roaster on development progress.",
    ]
    if elapsed_seconds is not None:
        minutes = int(elapsed_seconds) // 60
        seconds = int(elapsed_seconds) % 60
        system_parts.append(f"Time since charge: {minutes}:{seconds:02d}.")
    if phase:
        phase_context = {
            "drying": "Drying phase: beans lose moisture, transition from green to yellow to tan.",
            "maillard": "Maillard phase: browning reactions, beans go from tan to light brown to medium brown.",
            "development": "Development phase: after first crack, beans darken rapidly from medium to dark brown.",
            "cooling": "Cooling phase: roast is complete, beans should not darken further.",
        }
        system_parts.append(phase_context.get(phase, f"Current phase: {phase}."))

    system = " ".join(system_parts)

    question = (
        "Assess these coffee beans. Respond in this exact format:\n"
        "COLOR: <1-2 sentence color description using roasting terminology>\n"
        "SCORE: <number 1-10>\n"
        "UNIFORMITY: <1 sentence on color consistency across beans>\n"
        "\n"
        "Development score scale:\n"
        " 1 = green, raw, unroasted\n"
        " 2 = pale yellow, early drying\n"
        " 3 = tan, gold, late drying\n"
        " 4 = light brown, cinnamon roast\n"
        " 5 = medium brown, city roast\n"
        " 6 = medium-dark brown, full city\n"
        " 7 = dark brown, matte surface, light roast end\n"
        " 8 = dark brown, slight oil sheen, Vienna\n"
        " 9 = very dark, oily surface, French\n"
        "10 = nearly black, very oily, Italian/Spanish"
    )

    response = ask_about_image(image_path, question, system_prompt=system)
    if not response:
        return None

    # Parse structured response
    result = {
        "color_assessment": "",
        "development_score": 0,
        "uniformity": "",
    }

    for line in response.strip().split("\n"):
        line = line.strip()
        if line.startswith("COLOR:"):
            result["color_assessment"] = line[6:].strip()
        elif line.startswith("SCORE:"):
            try:
                # Handle formats like "8", "8/10", "8 out of 10"
                score_text = line[6:].strip().split()[0]
                score_text = score_text.split("/")[0]  # strip "/10" if present
                result["development_score"] = int(score_text)
            except (ValueError, IndexError):
                result["development_score"] = 0
        elif line.startswith("UNIFORMITY:"):
            result["uniformity"] = line[11:].strip()

    # Fallback: if parsing failed, use the raw response as color assessment
    if not result["color_assessment"] and response:
        result["color_assessment"] = response.strip()

    return result
