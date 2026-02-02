import sys
import subprocess
import os
import platform

def install_requirements():
    print("Checking dependencies...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
    except subprocess.CalledProcessError:
        print("Error installing dependencies!")
        sys.exit(1)

def fix_permissions():
    """Fixes ownership of the HuggingFace cache directory if it exists."""
    if platform.system() != "Windows":
        # Get the actual user (not root if run with sudo)
        sudo_user = os.environ.get("SUDO_USER")
        if sudo_user:
            user = sudo_user
        else:
            user = os.environ.get("USER")
            
        home = os.path.expanduser(f"~{user}")
        cache_dir = os.path.join(home, ".cache", "huggingface")
        
        if os.path.exists(cache_dir):
            print(f"Fixing permissions for {cache_dir}...")
            try:
                # We need to run chown. If we are root, we can do it.
                # If we are not root, we might not be able to fix root-owned files.
                subprocess.call(["chown", "-R", user, cache_dir])
            except Exception as e:
                print(f"Could not fix permissions: {e}")

if __name__ == "__main__":
    print("--- Omni Setup ---")
    
    # 1. Install Requirements
    install_requirements()
    
    # 2. Fix Permissions (common issue with sudo runs)
    if platform.system() != "Windows":
        fix_permissions()
        
    print("Setup complete. Ready to launch.")
