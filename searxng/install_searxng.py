import io
import zipfile
import requests
import os
import shutil

# Define URL and target directory
ZIP_URL = "https://github.com/searxng/searxng/archive/refs/heads/master.zip"
TARGET_DIR = os.path.join(os.getcwd(), "searxng_local")

def download_and_extract():
    print(f"Downloading SearXNG from {ZIP_URL}...")
    try:
        r = requests.get(ZIP_URL)
        r.raise_for_status()
    except Exception as e:
        print(f"Failed to download: {e}")
        return

    print("Extracting files...")
    if os.path.exists(TARGET_DIR):
        shutil.rmtree(TARGET_DIR)
    
    os.makedirs(TARGET_DIR, exist_ok=True)

    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        # We only want 'searx/', 'requirements.txt', 'setup.py', 'README.md'
        # The zip has a root folder 'searxng-master/'
        
        for file_info in z.infolist():
            # Filter out the invalid paths (like those with colons or other weird chars)
            # Windows: <>:"/\|?* are reserved
            if any(c in file_info.filename for c in '<>:"|?*'):
                continue
            
            # We want to extract content of searxng-master/ to TARGET_DIR/
            parts = file_info.filename.split('/')
            if len(parts) > 1:
                # remove the first part (searxng-master)
                rel_path = "/".join(parts[1:])
                if not rel_path: continue
                
                # Check if we need this file
                # We need 'searx/' folder and some root files
                if rel_path.startswith("searx/") or rel_path in ["requirements.txt", "setup.py", "README.md", "manage.py"]:
                     # Construct target path
                    target_path = os.path.join(TARGET_DIR, *parts[1:])
                    
                    # Ensure directory exists
                    os.makedirs(os.path.dirname(target_path), exist_ok=True)
                    
                    if not file_info.is_dir():
                        with open(target_path, "wb") as f:
                            f.write(z.read(file_info))

    print(f"Extracted to {TARGET_DIR}")

if __name__ == "__main__":
    download_and_extract()
