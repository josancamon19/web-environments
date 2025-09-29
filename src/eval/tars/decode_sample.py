import json
import base64
from pathlib import Path


def decode_base64_images(json_file_path: str, output_dir: str) -> None:
    """
    Decode base64 encoded images from JSON file and save as PNG files.

    Args:
        json_file_path: Path to the JSON file containing messages with base64 images
        output_dir: Directory to save the decoded PNG files
    """
    # Ensure output directory exists
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # Read the JSON file
    with open(json_file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Extract images from messages
    image_count = 0

    # Assuming the JSON structure has a list of messages or conversations
    if isinstance(data, list):
        messages = data
    elif isinstance(data, dict) and "messages" in data:
        messages = data["messages"]
    else:
        # Try to find messages in any nested structure
        messages = []

        def extract_messages(obj):
            if isinstance(obj, dict):
                if "messages" in obj:
                    messages.extend(obj["messages"])
                else:
                    for value in obj.values():
                        extract_messages(value)
            elif isinstance(obj, list):
                for item in obj:
                    extract_messages(item)

        extract_messages(data)

    # Process each message to find image content
    for message in messages:
        if isinstance(message, dict) and "content" in message:
            content = message["content"]

            # Handle different content formats
            if isinstance(content, list):
                # Content is a list of parts (text and images)
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "image_url":
                        image_url = part.get("image_url", {}).get("url", "")
                        if image_url.startswith("data:image/png;base64,"):
                            base64_data = image_url.split(",", 1)[1]
                            save_image(base64_data, output_path, image_count)
                            image_count += 1
            elif isinstance(content, str):
                # Content might be a string with embedded images
                # Check if it contains image data (though unlikely based on our grep results)
                pass

    print(f"Decoded and saved {image_count} images to {output_dir}")


def save_image(base64_data: str, output_dir: Path, index: int) -> None:
    """
    Decode base64 data and save as PNG file.

    Args:
        base64_data: Base64 encoded image data
        output_dir: Directory to save the image
        index: Index for naming the file
    """
    try:
        # Decode base64 data
        image_data = base64.b64decode(base64_data)

        # Save as PNG file
        filename = f"{index:04d}.png"
        filepath = output_dir / filename

        with open(filepath, "wb") as f:
            f.write(image_data)

        print(f"Saved image: {filepath}")

    except Exception as e:
        print(f"Error saving image {index}: {e}")


def main():
    # Define paths
    script_dir = Path(__file__).parent
    json_file = script_dir / "test_messages.json"
    output_dir = script_dir / "images"

    # Check if JSON file exists
    if not json_file.exists():
        print(f"Error: {json_file} not found")
        return

    # Decode images
    decode_base64_images(str(json_file), str(output_dir))


if __name__ == "__main__":
    main()
