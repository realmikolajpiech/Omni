import os
import sys
import secrets
import subprocess
import time

def main():
    # Get the directory of this script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Path to the extracted searxng source
    searxng_src_dir = os.path.join(script_dir, 'searxng_local')
    
    # Ensure the source directory exists
    if not os.path.exists(searxng_src_dir):
        print(f"Error: SearXNG source directory not found at {searxng_src_dir}")
        sys.exit(1)

    # Set up environment variables
    env = os.environ.copy()
    
    # Add searxng_local to PYTHONPATH so 'searx' module can be found
    python_path = env.get('PYTHONPATH', '')
    if python_path:
        env['PYTHONPATH'] = f"{searxng_src_dir}{os.pathsep}{python_path}"
    else:
        env['PYTHONPATH'] = searxng_src_dir
        
    # Generate a random secret key if one isn't set
    if 'SEARXNG_SECRET' not in env:
        env['SEARXNG_SECRET'] = secrets.token_hex(32)
        print(f"Generated temporary SEARXNG_SECRET: {env['SEARXNG_SECRET']}")

    # Set settings path explicitly if needed, though default usually works if running from right dir
    settings_path = os.path.join(searxng_src_dir, 'searx', 'settings.yml')
    if os.path.exists(settings_path):
        env['SEARXNG_SETTINGS_PATH'] = settings_path
        print(f"Using settings file: {settings_path}")
    
    # Command to start the webapp
    cmd = [sys.executable, '-m', 'searx.webapp']
    
    # Set environment variables for SearXNG
    env['SEARXNG_PORT'] = '8080'
    env['SEARXNG_BIND_ADDRESS'] = '127.0.0.1'
    
    print("Starting SearXNG locally...")
    print(f"Command: {' '.join(cmd)}")
    print(f"PYTHONPATH: {env['PYTHONPATH']}")
    
    try:
        # Start the process
        # We don't use shell=True to keep it cleaner, unless on Windows we need it for some path reason.
        # But sys.executable should be fine.
        process = subprocess.Popen(cmd, env=env, cwd=searxng_src_dir)
        
        print(f"SearXNG started with PID: {process.pid}")
        print("Waiting for server to initialize...")
        
        # Keep the script running to monitor the process, or just exit if we want it detached?
        # The user wants "automation", usually implying a service or a background task.
        # But for this session, I will keep it running in this script if run directly?
        # No, I will use RunCommand with blocking=False to run this script.
        # So this script should probably just run the subprocess and wait.
        process.wait()
        
    except KeyboardInterrupt:
        print("Stopping SearXNG...")
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        print("SearXNG stopped.")

if __name__ == '__main__':
    main()
