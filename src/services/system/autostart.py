import os
import sys
import logging
import winreg

# Add project root to path to allow importing config
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_dir)))
if project_root not in sys.path:
    sys.path.append(project_root)

import shutil
import pythoncom
from win32com.client import Dispatch

from src.core.config import PROJECT_ROOT, LOGO_PATH

APP_NAME = "OmniBrain"

def create_shortcut(target_path, arguments, shortcut_path, icon_path=None, description="Omni Brain"):
    try:
        shell = Dispatch('WScript.Shell')
        shortcut = shell.CreateShortcut(shortcut_path)
        shortcut.TargetPath = target_path
        shortcut.Arguments = arguments
        shortcut.WorkingDirectory = os.path.dirname(target_path)
        if icon_path and os.path.exists(icon_path):
            shortcut.IconLocation = icon_path
        shortcut.Description = description
        shortcut.Save()
        return True
    except Exception as e:
        logging.error(f"Failed to create shortcut: {e}")
        return False

def set_autostart(enable=True):
    """
    Adds or removes the application from Windows Registry Run key.
    """
    if sys.platform != "win32":
        logging.warning("Autostart is only supported on Windows.")
        return False

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_ALL_ACCESS)
    except OSError:
        try:
            key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path)
        except Exception as e:
            logging.error(f"Failed to open/create registry key: {e}")
            return False

    try:
        if enable:
            script_path = os.path.join(PROJECT_ROOT, "run.py")
            
            # Use pythonw.exe to avoid console window if possible
            python_exe = sys.executable
            if "python.exe" in python_exe:
                pythonw = python_exe.replace("python.exe", "pythonw.exe")
                if os.path.exists(pythonw):
                    python_exe = pythonw
            
            # Create a hidden shortcut in the project root
            shortcut_path = os.path.join(PROJECT_ROOT, "Omni.lnk")
            
            # We need to run pythonw.exe with the script as argument
            # But shortcuts work best if Target is the executable
            create_shortcut(
                target_path=python_exe,
                arguments=f'"{script_path}"',
                shortcut_path=shortcut_path,
                icon_path=LOGO_PATH,
                description="Omni AI Assistant"
            )
            
            # Register the SHORTCUT in registry, not the raw command
            # This allows Windows to see the Icon from the shortcut?
            # Actually, Registry Run keys don't always respect .lnk properties visually in Task Manager startup tab
            # BUT, putting the shortcut in the Startup Folder IS the better way for custom icons.
            
            # Switch to Startup Folder method for better Icon support
            startup_folder = os.path.join(os.getenv('APPDATA'), r'Microsoft\Windows\Start Menu\Programs\Startup')
            startup_shortcut = os.path.join(startup_folder, "Omni.lnk")
            
            # For Task Manager visibility, we might need a wrapper EXE or BAT, but LNK usually works.
            # If it doesn't show in Task Manager, it might be because it points to pythonw.exe directly.
            # Let's try pointing to a BAT file wrapper instead, which might be more "visible" as a startup item?
            # Or just accept that LNKs in Startup folder sometimes take a reboot to appear in Task Manager.
            
            # Let's try creating a small .vbs wrapper to launch it silently without console,
            # and point the shortcut to that. VBS scripts are often used for this.
            
            vbs_path = os.path.join(PROJECT_ROOT, "run_silent.vbs")
            with open(vbs_path, "w") as f:
                # WScript.Shell Run method: 0 = Hide Window
                # Launch Brain Service
                f.write(f'CreateObject("WScript.Shell").Run """{python_exe}"" ""{script_path}"" ""brain""", 0, False\n')
                # Launch UI (which will listen for hotkey)
                f.write(f'CreateObject("WScript.Shell").Run """{python_exe}"" ""{script_path}"" ""ui""", 0, False\n')
            
            # Use wscript.exe explicitly to run the VBS
            wscript_path = os.path.join(os.getenv('SystemRoot'), 'System32', 'wscript.exe')
            
            create_shortcut(
                target_path=wscript_path,
                arguments=f'"{vbs_path}"',
                shortcut_path=startup_shortcut,
                icon_path=LOGO_PATH,
                description="Omni AI Assistant"
            )
            
            logging.info(f"Autostart enabled via Startup Folder: {startup_shortcut}")
            print(f"Autostart enabled via Startup Folder: {startup_shortcut}")
            
            # Remove from Registry if it was there (cleanup old method)
            try:
                winreg.DeleteValue(key, APP_NAME)
            except: pass
            
        else:
            # Remove from Startup Folder
            startup_folder = os.path.join(os.getenv('APPDATA'), r'Microsoft\Windows\Start Menu\Programs\Startup')
            startup_shortcut = os.path.join(startup_folder, "Omni.lnk")
            if os.path.exists(startup_shortcut):
                os.remove(startup_shortcut)
                logging.info("Autostart disabled (Startup Shortcut removed)")
                print("Autostart disabled (Startup Shortcut removed)")

            # Also cleanup registry just in case
            try:
                winreg.DeleteValue(key, APP_NAME)
            except: pass
                
        winreg.CloseKey(key)
        return True
    except Exception as e:
        logging.error(f"Failed to set autostart: {e}")
        print(f"Failed to set autostart: {e}")
        return False

if __name__ == "__main__":
    # If run directly, default to enabling
    if len(sys.argv) > 1 and sys.argv[1] == "disable":
        set_autostart(False)
    else:
        set_autostart(True)
