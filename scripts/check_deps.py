import sys
import os
import subprocess
import importlib
import pprint
from packaging import version

REQUIRED_PACKAGES = {
    "httpx": "0.27.0",
    "aiofiles": "24.1.0",
}

def print_env_info():
    print("=== Python Environment Info ===")
    print(f"Executable: {sys.executable}")
    print(f"Prefix: {sys.prefix}")
    print(f"Base Prefix: {getattr(sys, 'base_prefix', '')}")
    print(f"VIRTUAL_ENV: {os.environ.get('VIRTUAL_ENV')}")
    print("sys.path:")
    pprint.pprint(sys.path)
    print("===============================")

def get_pip_list(cmd):
    try:
        output = subprocess.check_output(cmd, shell=True, text=True)
        pkgs = set()
        for line in output.splitlines():
            if line.strip() and not line.startswith("Package") and not line.startswith("---"):
                parts = line.split()
                if parts:
                    pkgs.add(parts[0].lower())
        return pkgs
    except Exception as e:
        print(f"WARNING: Failed to run '{cmd}': {e}")
        return set()

def suggest_activation():
    print("\nSUGGESTION:")
    if os.path.isdir(".venv"):
        print("It looks like you have a virtual environment in .venv/")
        print("Activate it with:\n  source .venv/bin/activate")
    else:
        print("Consider creating and activating a virtual environment:")
        print("  uv venv")
        print("  source .venv/bin/activate")
        print("Then install dependencies with:")
        print("  uv pip sync")
    print()

def main():
    print_env_info()

    uv_pkgs = get_pip_list("uv pip list")
    py_pkgs = get_pip_list("python3 -m pip list")

    if uv_pkgs and not py_pkgs.intersection(uv_pkgs):
        print("WARNING: It appears 'uv' has installed packages that are NOT visible to the current Python interpreter.")
        print("Your Python may not be running inside the expected virtual environment.")
        suggest_activation()

    all_ok = True
    for pkg, min_ver in REQUIRED_PACKAGES.items():
        if not check_package(pkg, min_ver):
            all_ok = False

    if not all_ok:
        print("One or more required packages are missing or outdated. Please install/update them before running the project.")
        sys.exit(1)
    else:
        print("All required dependencies are installed and meet minimum version requirements.")

def check_package(pkg_name, min_version):
    try:
        pkg = importlib.import_module(pkg_name)
    except ImportError:
        print(f"ERROR: Required package '{pkg_name}' is not installed.")
        return False

    try:
        pkg_version = pkg.__version__
    except AttributeError:
        print(f"WARNING: Could not determine version of '{pkg_name}'. Assuming compatible.")
        return True

    if version.parse(pkg_version) < version.parse(min_version):
        print(f"ERROR: '{pkg_name}' version {pkg_version} is installed, but >= {min_version} is required.")
        return False

    return True

if __name__ == "__main__":
    main()