import sys
try:
    from Foundation import NSUserDefaults
    defaults = NSUserDefaults.standardUserDefaults()
    style = defaults.stringForKey_("AppleInterfaceStyle")
    print(f"AppleInterfaceStyle: '{style}'")
    
    if style == "Dark":
        print("Detected: Dark")
    else:
        print("Detected: Light")
        
except ImportError:
    print("PyObjC Foundation not found")
except Exception as e:
    print(f"Error: {e}")
